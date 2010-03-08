# -*- coding: utf-8 -*-
"""The application's model objects"""

from .session import ProjectSession
from .project import Neighborhood, NeighborhoodFile, Project, ProjectFile, AppConfig, SearchConfig, ScheduledMessage
from .discuss import Discussion, Thread, PostHistory, Post, Attachment
from .artifact import Artifact, Message, VersionedArtifact, Snapshot, ArtifactLink, nonce, Feed
from .auth import User, ProjectRole, OpenId, EmailAddress
from .openid_model import OpenIdStore, OpenIdAssociation, OpenIdNonce
from .filesystem import File
from .tag import TagEvent, Tag, UserTags

from .types import ArtifactReference, ArtifactReferenceType

from .session import main_doc_session, main_orm_session
from .session import project_doc_session, project_orm_session
from .session import artifact_orm_session

from ming.orm.mapped_class import MappedClass
MappedClass.compile_all()
