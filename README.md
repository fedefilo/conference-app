
**Task 1**
models.py
conference.py, lines 482-729 

To implement the session and speaker entities, I built the following classes:
- Session: contains all the required attributes for a session, including its websafeKey for easier reference later, and a enumfield for the session type
- SessionType: required to implement an enumfield
- SessionForm: protoRPC message form for inbound and outbound API messages
- SessionForms: for communicating repeated SessionForm objects.
- Speaker: an entity that includes the speaker firstname, lastname and institution and its own websafekey for easier reference.
- SpeakerForm: protoRPC message form for inbound and outbound API messages
- SpeakerForms: for communicating repeated SpeakerForm objects.

Functions defined include:
- _createSessionObject to create the object from the request passed by createSession API method
- _copySessionToForm to copy the session object content to a SessionForm that can be transmitted via protoRPC.

All API methods described in the project specifications were created.

Regarding the speaker's entity implementation, 
- Since a session can have more than one speaker, an array of strings (StringProperty(repeated=True)) is included in the Session object. It stores the websafeKey of the speakers featured in the session.
- the endpoint createSpeaker() creates a speaker object in datastore receiving information from a SpeakerForm message. Open to all logged users.
- the endpoint addSpeakerToSession(websafeSpeakerKey, websafeSessionKey) adds the speaker passed with the websafekey to the array of speakers currently featured in the session. Open only to the creator of the session entity.
- the endpoint deleteSpeakerFromSession(websafeSpeakerKey, websafeSessionKey) deletes the speaker passed with the websafekey from the array of speakers currently featured in the session. Open only to the creator of the session entity.
- the endpoint listSpeakers() returns a list of all the speakers in the DB.

** Task 2 **
conference.py lines 739-794

All three API endpoints were implemented.

** Task 3 **
conference.py, lines 794-874

Three additional queries were implemented:
- listSpeakersinWishlist() returns the speakers featured in the sessions currently in the logged user's wishlist
- popularSpeakers() returns the speakers participating in two or more sessions across all conferences.
- successfulConferences() returns the conferences that have only available less than 5% of its seats.
- earlynonworksop(websafeConferenceKey) returns the non-workshop sessions before 7pm in a given conference.

The problem with the mentioned query is that it involves 2 inequality filters: a NOT and a LESS THAN operator. This is not allowed by datastore. The proposed (and implemented) solution involves filtering only by one of these operators (LESS THAN) through the query API. The second filter (session_type) is applied using lists and comparisons in python. The solution works fine.
 

** Task 4 **
main.app
app.yaml
conference.py, lines 536-539, 879-925.

The getFeaturedSpeaker() was correctly implemented. Since a session may involve more than one speaker, and these speakers may also both be featured in other sessions in the conference, the announcement stored memcache may include information for more than one speaker. 



--------------

App Engine application for the Udacity training course.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool
# conference-app
