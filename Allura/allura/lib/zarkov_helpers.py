#       Licensed to the Apache Software Foundation (ASF) under one
#       or more contributor license agreements.  See the NOTICE file
#       distributed with this work for additional information
#       regarding copyright ownership.  The ASF licenses this file
#       to you under the Apache License, Version 2.0 (the
#       "License"); you may not use this file except in compliance
#       with the License.  You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#       Unless required by applicable law or agreed to in writing,
#       software distributed under the License is distributed on an
#       "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#       KIND, either express or implied.  See the License for the
#       specific language governing permissions and limitations
#       under the License.

import calendar
from datetime import datetime, timedelta

try:
    import zmq
except ImportError:
    zmq = None
import bson

class ZarkovClient(object):

    def __init__(self, addr):
        context = zmq.Context.instance()
        self._sock = context.socket(zmq.PUSH)
        self._sock.connect(addr)

    def event(self, type, context, extra=None):
        obj = dict(
            type=type, context=context, extra=extra)
        self._sock.send(bson.BSON.encode(obj))

def zero_fill_zarkov_result(zarkov_data, period, start_date, end_date):
    """Return a new copy of zarkov_data (a dict returned from a zarkov
    query) with the timeseries data zero-filled for missing dates.

    Args:
        zarkov_data (dict): A Zarkov query result.
        period (str): 'month' or 'date' for monthly or daily timestamps
        start_date (datetime or str): Start of the date range. If a str is
            passed, it must be in %Y-%m-%d format.
        end_date (datetime or str): End of the date range. If a str is
            passed, it must be in %Y-%m-%d format.

    Returns:
        dict. A new copy of zarkov_data, zero-filled.

    """
    d = zarkov_data.copy()
    if isinstance(start_date, basestring):
        start_date = datetime.strptime(start_date, '%Y-%m-%d')
    if isinstance(end_date, basestring):
        end_date = datetime.strptime(end_date, '%Y-%m-%d')
    for query in zarkov_data.iterkeys():
        for series in zarkov_data[query].iterkeys():
            d[query][series] = zero_fill_time_series(d[query][series],
                                    period, start_date, end_date)
    return d

def zero_fill_time_series(time_series, period, start_date, end_date):
    """Return a copy of time_series after adding [timestamp, 0] pairs for
    each missing timestamp in the given date range.

    Args:
        time_series (list): A list of [timestamp, value] pairs, e.g.:
            [[1306886400000.0, 1], [1309478400000.0, 0]]
        period (str): 'month' or 'date' for monthly or daily timestamps
        start_date (datetime): Start of the date range.
        end_date (datetime or str): End of the date range.

    Returns:
        list. A new copy of time_series, zero-filled.

    If you want to zero-fill an entire zarkov result, you should use
    :func:`zero_fill_zarkov_result` instead, which will call this function
    for each timeseries list in the zarkov result.

    """
    new_series = dict(time_series)
    if period == 'month':
        date = start_date
        while date <= end_date:
            ts = to_utc_timestamp(date)
            if ts not in new_series:
                new_series[ts] = 0
            # next month
            if date.month == 12:
                date = date.replace(year=date.year+1, month=1)
            else:
                date = date.replace(month=date.month+1)
    else: # daily
        days = (end_date - start_date).days + 1
        periods = range(0, days)
        for dayoffset in periods:
            date = start_date + timedelta(days=dayoffset)
            ts = to_utc_timestamp(date)
            if ts not in new_series:
                new_series[ts] = 0
    return sorted([[k, v] for k, v in new_series.items()])

def to_utc_timestamp(d):
    """Return UTC unix timestamp representation of d (datetime)."""
    # http://stackoverflow.com/questions/1077285/how-to-specify-time-zone-utc-when-converting-to-unix-time-python
    return calendar.timegm(d.utctimetuple()) * 1000.0
