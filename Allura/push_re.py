import os
import re
import shlex
import string
import subprocess
from collections import defaultdict
from ConfigParser import ConfigParser
from datetime import date
from urlparse import urljoin

from allura.config import middleware
from allura.lib import rest_api

DEBUG=1
CP = ConfigParser()

re_ticket_ref = re.compile(r'\[#\d+\]')

CRED={}

def main():
    CP.read(os.path.join(os.environ['HOME'], '.forgepushrc'))
    engineer = option('re', 'engineer', 'Name of engineer pushing: ')
    api_key = option('re', 'api_key', 'Forge API Key:')
    secret_key = option('re', 'secret_key', 'Forge Secret Key:')
    CRED['api_key'] = api_key
    CRED['secret_key'] = secret_key
    text, tag = make_ticket_text(engineer)
    print '*** Create a ticket on SourceForge (https://sourceforge.net/p/allura/tickets/new/) with the following contents:'
    print '*** Summary: Production Push (R:%s, D:%s) - allura' % (
        tag, date.today().strftime('%Y%m%d'))
    print '---BEGIN---'
    print text
    print '---END---'
    raw_input("Verify that there are no new dependencies, or RPM's are build for all deps...")
    raw_input("Verify that a new sandbox builds starts without engr help...")
    raw_input('When this is done, create a SOG Trac ticket'
              ' (https://control.sog.geek.net/sog/trac/newticket?keywords=LIAISON) with the'
              ' same contents...')
    raw_input('Now link the two tickets...')
    newforge_num = raw_input('What is the newforge ticket number? ')
    command('git', 'tag', '-a', '-m', '[#%s] - Push to RE' % newforge_num, tag, 'master')
    command('git', 'push', 'origin', 'master')
    command('git', 'push', 'live', 'master')
    command('git', 'push', '--tags', 'origin')
    command('git', 'push', '--tags', 'live')
    raw_input('Now go to the sog-engr channel and let them know that %s is ready'
              ' for pushing (include the JIRA ticket #' % tag)
    raw_input('Make sure SOG restarted reactors and web services.')
    CP.write(open(os.path.join(os.environ['HOME'], '.forgepushrc'), 'w'))
    print "You're done!"

def make_ticket_text(engineer):
    tag_prefix = date.today().strftime('release_%Y%m%d')
    # get release tag
    existing_tags_today = command('git tag -l %s*' % tag_prefix)
    if existing_tags_today:
        tag = '%s.%.2d' % (tag_prefix, len(existing_tags_today))
    else:
        tag = tag_prefix
    last_release = command('git tag -l release_*')
    if last_release: last_release = last_release[-1]
    else: last_release = ''
    changes = command(
            'git', 'log', "--format=* %h %s", last_release.strip() + '..')
    assert changes, 'There were no commits found; maybe you forgot to merge dev->master?'
    changelog = ''.join(changes)
    changes = ''.join(format_changes(changes))
    print 'Changelog:\n%s' % changelog
    print 'Tickets:\n%s' % changes
    prelaunch = []
    postlaunch = []
    needs_reactor_setup = raw_input('Does this release require a reactor_setup? [n]')
    needs_flyway = raw_input('Does this release require a migration? [y]')
    if needs_reactor_setup[:1].lower() in ('y', '1'):
        postlaunch.append('* service reactor stop')
        postlaunch.append('* allurapaste reactor_setup /var/local/config/production.ini')
        postlaunch.append('* service reactor start')
    if needs_flyway[:1].lower() in ('', 'y', '1'):
        prelaunch.append('* dump the database in case we need to roll back')
        postlaunch.append('* allurapaste flyway --url mongo://sfn-mongo:27017/')
    postlaunch.append('* allurapaste ensure_index /var/local/config/production.ini')
    if postlaunch:
        postlaunch = [ 'From sfu-scmprocess-1 do the following:\n' ] + postlaunch
        postlaunch = '\n'.join(postlaunch)
    else:
        postlaunch = '-none-'
    if prelaunch:
        prelaunch = [ 'From sfn-mongo do the following:\n' ] + prelaunch
        prelaunch = '\n'.join(prelaunch)
    else:
        prelaunch = '-none-'
    return TICKET_TEMPLATE.substitute(locals()), tag

def format_changes(changes):
    ticket_groups = defaultdict(list)
    for change in changes:
        for m in re_ticket_ref.finditer(change):
            ticket_groups[m.group(0)].append(change)
    cli = rest_api.RestClient(
        base_uri='http://sourceforge.net', **CRED)
    for ref, commits in sorted(ticket_groups.iteritems()):
        ticket_num = ref[2:-1]
        ticket = cli.request(
            'GET',
            urljoin('/rest/p/allura/tickets/', str(ticket_num)) + '/')['ticket']
        verb = {
            'validation': 'Fix',
            'closed': 'Fix' }.get(ticket['status'], 'Address')
        yield ' * %s %s: %s\n' % (verb, ref, ticket['summary'])

def command(*args):
    if len(args) == 1 and isinstance(args[0], basestring):
        argv = shlex.split(args[0])
    else:
        argv = list(args)
    if DEBUG:
        print ' '.join(argv)
        raw_input('Press enter to run this command...')
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    rc = p.wait()
    if rc != 0:
        print 'Error running %s' % ' '.join(argv)
        import pdb; pdb.set_trace()
    return p.stdout.readlines()

def option(section, key, prompt=None):
    if not CP.has_section(section):
        CP.add_section(section)
    if CP.has_option(section, key):
        value = CP.get(section, key)
    else:
        value = raw_input(prompt or ('%s: ' % key))
        CP.set(section, key, value)
    return value

TICKET_TEMPLATE=string.Template('''{{{
#!push

(engr) Name of Engineer pushing: $engineer
(engr) Which code tree(s): allura
(engr) Is configtree to be pushed?: no
(engr) Which release/revision is going to be synced?: $tag
(engr) Itemized list of changes to be launched with sync:

$changes

Pre-launch dependencies:

$prelaunch

Post-launch dependencies:

$postlaunch

(engr) Approved for release (Dean/Dave/John): None
(sog) Outcome of sync:
}}}''')

if __name__ == '__main__':
    main()
