#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import StringMessage
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session
from models import SessionForm
from models import SessionForms
from models import SessionType
from models import Speaker
from models import SpeakerForm
from models import SpeakerForms

from settings import WEB_CLIENT_ID, ANDROID_AUDIENCE, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID

from utils import getUserId

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"
ANNOUNCEMENT_TPL = ('Last chance to attend! The following conferences '
                    'are nearly sold out: %s')
# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": ["Default", "Topic"],
}

DEAFULTS_SESSION = {
    "highlights": "No highlights",
    "speakers": [],
    "duration": 1,
    "date": "2000-12-12",
    "startTime": "12:00",
}


OPERATORS = {
    'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
}

FIELDS = {
    'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
}

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1, required=True),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

CONF_AND_TYPE_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1, required=True),
    session_type=messages.EnumField(SessionType, 2, required=True),
)

SPK_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeSpeakerKey=messages.StringField(1, required=True),
)

SPK_SESS_REQUEST = endpoints.ResourceContainer(
    websafeSessionKey=messages.StringField(1, required=True),
    websafeSpeakerKey=messages.StringField(2, required=True),
)

SESS_REQUEST = endpoints.ResourceContainer(
    websafeSessionKey=messages.StringField(1, required=True),
)


# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
               allowed_client_ids=[
                   WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
               scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException(
                "Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound
        # Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on
        # start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(
                data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(
                data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
                              'conferenceInfo': repr(request)},
                      url='/tasks/send_confirmation_email'
                      )
        return request

    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
                      http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
                      path='conference/{websafeConferenceKey}',
                      http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='getConferencesCreated',
                      http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(
                conf, getattr(prof, 'displayName')) for conf in confs]
        )

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(
                filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q

    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name)
                     for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException(
                    "Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is
                # performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException(
                        "Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
                      path='queryConferences',
                      http_method='POST',
                      name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId))
                      for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in
                   conferences]
        )


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(
                        TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf

    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key=p_key,
                displayName=user.nickname(),
                mainEmail=user.email(),
                teeShirtSize=str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile

    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        # if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        # else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)

    @endpoints.method(message_types.VoidMessage, ProfileForm,
                      path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()

    @endpoints.method(ProfileMiniForm, ProfileForm,
                      path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = ANNOUNCEMENT_TPL % (
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/get',
                      http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


#  ------ Create Sessions ----------

    def _createSessionObject(self, request):
        """Create Session object from SessionForm that includes confKey, returning SessionForm/request."""
        # check if user is logged in and is the creator of the conference
        # object.
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.websafeConferenceKey:
            raise endpoints.NotFoundException(
                'No conference key given')

        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if user_id != conf.organizerUserId:
            raise endpoints.UnauthorizedException(
                'Only the creator of the conference can add sessions')

        # get Conference object from request; bail if not found
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # Check required fields
        if not request.name:
            raise endpoints.BadRequestException(
                "Session 'name' field required")

        # copy SessionForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}

        # add default values for those missing (both data model & outbound
        # Message)
        for df in DEAFULTS_SESSION:
            if data[df] in (None, []):
                data[df] = DEAFULTS_SESSION[df]
                setattr(request, df, DEAFULTS_SESSION[df])

        # convert dates from strings to Date objects; set month based on
        # start_date
        if data['date']:
            data['date'] = datetime.strptime(
                data['date'][:10], "%Y-%m-%d").date()

        # convert time string into time object
        if data['startTime']:
            data['startTime'] = datetime.strptime(
                data['startTime'], '%H:%M').time()

        # allocate key based on unique numerical ID
        # and set conference as ancestor of the session object
        s_id = Session.allocate_ids(size=1, parent=conf.key)[0]
        s_key = ndb.Key(Session, s_id, parent=conf.key)
        safekey = s_key.urlsafe()
        data['key'] = s_key

        # save the session's websafe key as an attribute for easier reference
        # later
        data['websafeSessionKey'] = safekey
        request.websafeSessionKey = safekey

        # create Session
        Session(**data).put()

        # create task for featured speaker endpoint (Task 4)
        taskqueue.add(params={'sess_key': data['websafeSessionKey']},
                      url='/tasks/featured_speaker'
                      )
        return request

    def _copySessionToForm(self, sess):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(sess, field.name):
                # convert date and time to string
                if field.name.endswith('date') or field.name.endswith('Time'):
                    setattr(sf, field.name, str(getattr(sess, field.name)))
                # special code for enum type
                elif field.name == 'session_type':
                    stype = getattr(sess, field.name)
                    setattr(sf, field.name, stype)
                else:
                    setattr(sf, field.name, getattr(sess, field.name))
        sf.check_initialized()
        return sf

    @endpoints.method(SessionForm, SessionForm, path='createSession',
                      http_method='POST', name='createSession')
    def createSession(self, request):
        """Create new session."""
        return self._createSessionObject(request)

    @endpoints.method(CONF_GET_REQUEST, SessionForms, path='getConferenceSessions',
                      http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Get all sessions in a given conference."""
        # get conference object from websafekey, raise exception if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        # query datastore by ancestor, since all sessions in a conference are
        # its children
        q = Session.query(ancestor=conf.key)

        # return the results of the query as an array of SessionForm
        return SessionForms(items=[self._copySessionToForm(cf) for cf in q])

    @endpoints.method(CONF_AND_TYPE_REQUEST, SessionForms, path='getConferenceSessionsByType',
                      http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Get all sessions in a given conference that match a prticular type."""
        # get conference object from websafekey, raise exception if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        # query DB for all sessions in the conference and refine by session
        # type
        q = Session.query(ancestor=conf.key)
        q = q.filter(Session.session_type == request.session_type)

        # return the results of the query as an array of SessionForm
        return SessionForms(items=[self._copySessionToForm(ss) for ss in q])

    @endpoints.method(SPK_GET_REQUEST, SessionForms, path='getConferenceSessionsBySpeaker',
                      http_method='GET', name='getConferenceSessionsBySpeaker')
    def getConferenceSessionsBySpeaker(self, request):
        """Get all sessions in which a particular speaker is featured across all conferences."""
        # query all sessions and refine by speaker using the speaker websafe
        # key
        q = Session.query()
        q = q.filter(request.websafeSpeakerKey == Session.speakers)
        return SessionForms(items=[self._copySessionToForm(ss) for ss in q])

# -------------- Speaker code (Task 1 extra credit)-----------------

    def _createSpeakerObject(self, request):
        # check user is logged
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # check all required fields are provided by user
        if not request.firstName or not request.lastName or not request.institution:
            raise endpoints.BadRequestException("All fields are required")

        # convert data from request into dict
        data = {field.name: getattr(request, field.name)
                for field in request.all_fields()}

        # allocate key based on unique numerical ID
        s_id = Speaker.allocate_ids(size=1)[0]
        spk_key = ndb.Key(Speaker, s_id)
        data['key'] = spk_key

        # save the session's websafe key as an attribute for easier reference
        # later
        safekey = spk_key.urlsafe()
        data['websafeKey'] = safekey

        # Save into Datastore
        Speaker(**data).put()
        return request

    def _copySpeakerToForm(self, spk):
        ''' Copy Speaker object data into Speaker protorpc form'''
        spkf = SpeakerForm()
        for field in spkf.all_fields():
            if hasattr(spk, field.name):
                setattr(spkf, field.name, getattr(spk, field.name))
        spkf.check_initialized()
        return spkf

    @endpoints.method(SpeakerForm, SpeakerForm, path='createSpeaker',
                      http_method='POST', name='createSpeaker')
    def createSpeaker(self, request):
        """Create new speaker."""
        return self._createSpeakerObject(request)

    @endpoints.method(message_types.VoidMessage, SpeakerForms, path='listSpeakers',
                      http_method='GET', name='listSpeakers')
    def listSpeakers(self, request):
        """List all speakers."""
        q = Speaker.query()
        return SpeakerForms(items=[self._copySpeakerToForm(spk) for spk in q])

    @endpoints.method(SPK_SESS_REQUEST, SessionForm, path='addSpeakerToSession',
                      http_method='POST', name='addSpeakerToSession')
    def addSpeakerToSession(self, request):
        """Add Speaker to Session."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get necessary object and check their existence
        sess = ndb.Key(urlsafe=request.websafeSessionKey).get()
        spk = ndb.Key(urlsafe=request.websafeSpeakerKey).get()
        conf = ndb.Key(urlsafe=request.websafeSessionKey).parent().get()
        if not sess:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % request.websafeSessionKey)
        if not spk:
            raise endpoints.NotFoundException(
                'No speaker found with key: %s' % request.websafeSpeakerKey)

        # check user is the owner of the conference/session

        user_id = getUserId(user)
        if user_id != conf.organizerUserId:
            raise endpoints.UnauthorizedException(
                'Only the creator of the conference can add speakers to a session')

        # check if speaker already listed in session
        if request.websafeSpeakerKey in sess.speakers:
            raise endpoints.BadRequestException(
                'Speaker already in session')

        # add speaker to session
        sess.speakers.append(request.websafeSpeakerKey)

        # save and return a SessionForm with the updated session info
        sess.put()
        return self._copySessionToForm(sess)

    @endpoints.method(SPK_SESS_REQUEST, SessionForm, path='removeSpeakerFromSession',
                      http_method='DELETE', name='removeSpeakerFromSession')
    def removeSpeakerFromSession(self, request):
        """Remove Speaker from Session."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        if not request.websafeSessionKey or not request.websafeSpeakerKey:
            raise endpoints.BadRequestException(
                'You have to provide session and speaker key')

        # get necessary object and check their existence
        sess = ndb.Key(urlsafe=request.websafeSessionKey).get()
        spk = ndb.Key(urlsafe=request.websafeSpeakerKey).get()
        conf = ndb.Key(urlsafe=request.websafeSessionKey).parent().get()
        if not sess:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % request.websafeSessionKey)
        if not spk:
            raise endpoints.NotFoundException(
                'No speaker found with key: %s' % request.websafeSpeakerKey)

        # check logged user is the owner of the conference/session
        user_id = getUserId(user)
        if user_id != conf.organizerUserId:
            raise endpoints.UnauthorizedException(
                'Only the creator of the conference can delete speakers from a session')

        # check if speaker already listed in session
        if request.websafeSpeakerKey not in sess.speakers:
            raise endpoints.BadRequestException(
                'Speaker not in session!')

        # add speaker to session
        sess.speakers.remove(request.websafeSpeakerKey)

        # save and return a SessionForm with the updated session info
        sess.put()
        return self._copySessionToForm(sess)

# ---------------- Session Wishlist (Task 2)-----------------------

    @endpoints.method(SESS_REQUEST, ProfileForm, path='addSessionToWishlist',
                      http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """Adds a session to the wishlist of the logged user"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        profile = self._getProfileFromUser()

        if request.websafeSessionKey in profile.wishlist:
            raise endpoints.BadRequestException("Session already in wishlist")

        # check if the user has registered for the conference
        conf = ndb.Key(urlsafe=request.websafeSessionKey).parent().get()
        if not conf:
            raise endpoints.NotFoundException('Please check the sessionkey')
        if conf.key.urlsafe() not in profile.conferenceKeysToAttend:
            raise endpoints.BadRequestException(
                "You have to register for the conference first")

        # adds session to wishlist
        profile.wishlist.append(request.websafeSessionKey)
        profile.put()
        return self._copyProfileToForm(profile)

    @endpoints.method(message_types.VoidMessage, SessionForms, path='getSessionsInWishlist',
                      http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """Returns the sessions currently in the wishlist of the logged user"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        profile = self._getProfileFromUser()
        if not profile.wishlist:
            raise endpoints.BadRequestException("No sessions in wishlist")

        # retrieves all sessions by its key and builds a list
        session_list = []
        for sess in profile.wishlist:
            obj = ndb.Key(urlsafe=sess).get()
            session_list.append(obj)
        return SessionForms(items=[self._copySessionToForm(sess) for sess in session_list])

    @endpoints.method(SESS_REQUEST, ProfileForm, path='deleteSessionInWishlist',
                      http_method='DELETE', name='deleteSessionInWishlist')
    def deleteSessionInWishlist(self, request):
        """Delete session from users' wishlist"""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        profile = self._getProfileFromUser()

        if request.websafeSessionKey not in profile.wishlist:
            raise endpoints.BadRequestException("Session not in wishlist")
        profile.wishlist.remove(request.websafeSessionKey)
        profile.put()
        return self._copyProfileToForm(profile)


# ------ Additional Queries (task 3) ---------------------

    @endpoints.method(message_types.VoidMessage, SpeakerForms, path='listSpeakersInWishlist',
                      http_method='GET', name='listSpeakersInWishlist')
    def listSpeakersInWishlist(self, request):
        """List all speakers that are featured in the sessions currently in the logged user's wishlist."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        profile = self._getProfileFromUser()
        if not profile.wishlist:
            raise endpoints.BadRequestException("No sessions in wishlist")

        # creates a list with the speaker objects references in the sessions
        # whose key is in the user's wishlis
        speaker_list = []
        for sess_key in profile.wishlist:
            session = ndb.Key(urlsafe=sess_key).get()
            for speaker_key in session.speakers:
                spk_obj = ndb.Key(urlsafe=speaker_key).get()
                # avoid repetitions
                if spk_obj not in speaker_list:
                    speaker_list.append(spk_obj)
        return SpeakerForms(items=[self._copySpeakerToForm(spk) for spk in speaker_list])

    @endpoints.method(message_types.VoidMessage, SpeakerForms, path='popularSpeakers', http_method='GET',
                      name='popularSpeakers')
    def popularSpeakers(self, request):
        """ List speakers participating in two or more sessions across all conferences """
        q = Speaker.query()
        spk_participations = {}

        # builds a dict to include each speaker's key and the times it appears
        for spk in q:
            spk_participations[spk.key.urlsafe()] = 0

        # retrieve all sessions and iterate through the speaker's array
        q = Session.query()
        for sess in q:
            for speaker in sess.speakers:
                # increment the counter when the speaker's key appears
                spk_participations[speaker] += 1

        popularSpeakers = []
        # search the dict for the speakers' appearing more than once
        for (speaker, appeareances) in spk_participations.items():
            if appeareances >= 2:
                # append the speakers object to the list
                popularSpeakers.append(ndb.Key(urlsafe=speaker).get())
        return SpeakerForms(items=[self._copySpeakerToForm(spk) for spk in popularSpeakers])

    @endpoints.method(message_types.VoidMessage, ConferenceForms, path='successfulConferences',
                      http_method='GET', name='successfulConferences')
    def successfulConferences(self, request):
        """ List conferences with more than 95 percent of its seats occupied"""
        q = Conference.query()
        conf_list = []
        for conf in q:
            # calculate threshold level for inclusion
            fivepercent = 0.05 * conf.maxAttendees
            if conf.seatsAvailable < fivepercent:
                conf_list.append(conf)
        return ConferenceForms(items=[self._copyConferenceToForm(conf, '') for conf in conf_list])

    @endpoints.method(CONF_GET_REQUEST, SessionForms, path='early-non-workshop/{websafeConferenceKey}',
                      http_method='GET', name='early-non-workshop')
    def earlynonworkshop(self, request):
        """ Returns all non workshop sessions before 19:00"""
        # queries all sessions
        q = Session.query()

        # restrict query to the given conference
        q = q.filter(Session.websafeConferenceKey ==
                     request.websafeConferenceKey)
        # applies first inequality filter
        q = q.filter(Session.startTime < datetime.strptime(
            "19:00", '%H:%M').time())

        # second inequality filter processed using programming languange and not query API
        # approach is building a list and checking inequalities as python
        # object properties
        results = []
        for sess in q:
            if sess.session_type != SessionType('WORKSHOP'):
                results.append(sess)

        return SessionForms(items=[self._copySessionToForm(sess) for sess in results])

# ----------------- Task 4 - Add a task ------------------------------

    @staticmethod
    def _cacheFeaturedSpeaker(sess_key):
        """Query DB for featured speakers & assign results to memcache."""

        # get session object
        ses_obj = ndb.Key(urlsafe=sess_key).get()

        # get conference object
        conf = ses_obj.key.parent().get()

        # get all sessions in the conference
        sessions = Session.query(ancestor=conf.key)

        # here I'll store the message to be set
        memcache_message = ''

        # for each speaker in participating in the session, check if featured
        # in other sessions in the same conf
        for newspeaker in ses_obj.speakers:
            # check if speaker key is present more than one time in all the
            # sessions
            counter = 0
            session_names = []
            for session in sessions:
                if newspeaker in session.speakers:
                    counter += 1
                    session_names.append(session.name)

            # has to be 2 or more, since the recently added session would also
            # be counted
            if counter > 1:
                # get speaker object
                spk_obj = ndb.Key(urlsafe=newspeaker).get()
                # build message
                fullname = spk_obj.firstName + " " + \
                    spk_obj.lastName + " (" + spk_obj.institution + ")"
                featuredsessions = ', '.join(session_names)
                addedmessage = "Speaker " + fullname + \
                    " is featured in the following sessions: " + featuredsessions
                memcache_message += addedmessage
            memcache_message += '.\n'
        # set announcement in memcache
        memcache.set("FEATURED SPEAKERS", memcache_message)
        return memcache_message

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='getFeaturedSpeaker',
                      http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get("FEATURED SPEAKERS") or "")


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser()  # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='conferences/attending',
                      http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser()  # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck)
                     for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId)
                      for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])
                                      for conf in conferences]
                               )

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
                      path='conference/{websafeConferenceKey}',
                      http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
                      path='filterPlayground',
                      http_method='GET', name='filterPlayground')
    def filterPlayground(self, request):
        """Filter Playground"""
        q = Conference.query()
        # field = "city"
        # operator = "="
        # value = "London"
        # f = ndb.query.FilterNode(field, operator, value)
        # q = q.filter(f)
        q = q.filter(Conference.city == "London")
        q = q.filter(Conference.topics == "Medical Innovations")
        q = q.filter(Conference.month == 6)

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, "") for conf in q]
        )


api = endpoints.api_server([ConferenceApi])  # register API
