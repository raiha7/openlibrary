"""Upstream customizations."""

import os.path
import web
import urllib, urllib2
import random
import hmac
import md5
import simplejson

from infogami import config
from infogami.core.code import view, edit
from infogami.utils import delegate, app, types
from infogami.utils.view import require_login, render, add_flash_message, public
from infogami.infobase import client
from infogami.infobase.client import ClientException
from infogami.utils.context import context

from infogami.plugins.api.code import jsonapi

from openlibrary.plugins.openlibrary.processors import ReadableUrlProcessor
from openlibrary.plugins.openlibrary import code as ol_code
from openlibrary.plugins.search.code import SearchProcessor

from openlibrary.i18n import gettext as _

import forms

if not config.get('coverstore_url'):
    config.coverstore_url = "http://covers.openlibrary.org"

class SubjectPlace(client.Thing):
    def _get_solr_result(self):
        if not self._solr_result:
            name = self.name or ""
            q = {'subjects': name, "facets": True}
            self._solr_result = SearchProcessor().search(q)
        return self._solr_result
        
    def get_related_subjects(self):
        # dummy subjects
        return [web.storage(name='France', key='/subjects/places/France'), web.storage(name='Travel', key='/subjects/Travel')]
        
    def get_covers(self, offset=0, limit=20):
        editions = self.get_editions(offset, limit)
        olids = [e['key'].split('/')[-1] for e in editions]
        
        try:
            url = '%s/b/query?cmd=ids&olid=%s' % (config.coverstore_url, ",".join(olids))
            data = urllib2.urlopen(url).read()
            cover_ids = simplejson.loads(data)
        except IOError, e:
            print >> web.debug, 'ERROR in getting cover_ids', str(e) 
            cover_ids = {}
            
        def make_cover(edition):
            edition = dict(edition)
            edition.pop('type', None)
            edition.pop('subjects', None)
            edition.pop('languages', None)
            
            olid = edition['key'].split('/')[-1]
            if olid in cover_ids:
                edition['cover_id'] = cover_ids[olid]
            
            return edition
            
        return [make_cover(e) for e in editions]
    
    def get_edition_count(self):
        d = self._get_solr_result()
        return d['matches']
        
    def get_editions(self, offset, limit=20):
        if self._solr_result and offset+limit < len(self._solr_result):
            result = self._solr_result[offset:offset+limit]
        else:
            name = self.name or ""
            result = SearchProcessor().search({"subjects": name, 'offset': offset, 'limit': limit})
        return result['docs']
        
    def get_author_count(self):
        d = self._get_solr_result()
        return len(d['facets']['authors'])
        
    def get_authors(self):
        d = self._get_solr_result()
        return [web.storage(name=a, key='/authors/OL1A', count=count) for a, count in d['facets']['authors']]
    
    def get_publishers(self):
        d = self._get_solr_result()
        return [web.storage(name=p, count=count) for p, count in d['facets']['publishers']]
    
client.register_thing_class('/type/place', SubjectPlace)

@public
def render_template(name, *a, **kw):
    return render[name](*a, **kw)
    
@public
def json_encode(d):
    return simplejson.dumps(d)

class static(delegate.page):
    path = "/(?:images|css|js)/.*"
    def GET(self):
        page = web.ctx.site.get(web.ctx.path)
        if page and page.type.key != '/type/delete':
            return self.delegate()
        elif web.input(m=None).m is not None:
            return self.delegate()
        else:
            raise web.seeother('/static/upstream' + web.ctx.path)

    def POST(self):
        return self.delegate()

    def delegate(self):
        cls, args = app.find_mode()
        method = web.ctx.method

        if cls is None:
            raise web.seeother(web.changequery(m=None))
        elif not hasattr(cls, method):
            raise web.nomethod(method)
        else:
            return getattr(cls(), method)(*args)

# overwrite ReadableUrlProcessor patterns for upstream
ReadableUrlProcessor.patterns = [
    (r'/books/OL\d+M', '/type/edition', 'title', 'untitled'),
    (r'/authors/OL\d+A', '/type/author', 'name', 'noname'),
    (r'/works/OL\d+W', '/type/work', 'title', 'untitled')
]

# Types for upstream paths
types.register_type('^/authors/[^/]*$', '/type/author')
types.register_type('^/books/[^/]*$', '/type/edition')
types.register_type('^/languages/[^/]*$', '/type/language')

types.register_type('^/subjects/places/[^/]*$', '/type/place')
types.register_type('^/subjects/people/[^/]*$', '/type/person')

# fix photo/cover url pattern
ol_code.Author.photo_url_patten = "%s/photo"
ol_code.Edition.cover_url_patten = "%s/cover"

# handlers for change photo and change cover

class change_cover(delegate.page):
    path = "(/books/OL\d+M)/cover"
    def GET(self, key):
        return ol_code.change_cover().GET(key)
    
class change_photo(change_cover):
    path = "(/authors/OL\d+A)/photo"

del delegate.modes['change_cover']     # delete change_cover mode added by openlibrary plugin

class subject_covers(delegate.page):
    path = "(/subjects/places/[^/]*)/covers"
    encoding = "json"
    
    @jsonapi
    def GET(self, key):
        page = web.ctx.site.get(key)
        if page is None:
            raise web.notfound("")
        else:
            i = web.input(offset=0, limit=20)
            try:
                offset = int(i.offset)
                limit = int(i.limit)
            except ValueError:
                return []
            return page.get_covers(offset, limit)

# fix addbook urls

class addbook(delegate.page):
    path = "/books/add"
    
    def GET(self):
        return render_template('books/add1')
        
    def POST(self):
        i = web.input(title='')
        page = web.ctx.site.new('/books/new', {'key': '/books/new', 'type': '/type/edition', 'title': ''})
        return render_template('books/add2', page)
    
class addauthor(ol_code.addauthor):
    path = "/authors/add"    

del delegate.pages['/addbook']
# templates still refers to /addauthor.
#del delegate.pages['/addauthor'] 

web.template.Template.globals['gettext'] = _
web.template.Template.globals['_'] = _

@web.memoize
@public
def vendor_js():
    pardir = os.path.pardir 
    path = os.path.abspath(os.path.join(__file__, pardir, pardir, pardir, pardir, 'static', 'upstream', 'js', 'vendor.js'))
    digest = md5.md5(open(path).read()).hexdigest()
    return '/js/vendor.js?v=' + digest

# account

def _generate_salted_hash(key, text, salt=None):
    salt = salt or hmac.HMAC(key, str(random.random())).hexdigest()[:5]
    hash = hmac.HMAC(key, salt + web.utf8(text)).hexdigest()
    return '%s$%s' % (salt, hash)
    
def _verify_salted_hash(key, text, hash):
    salt = hash.split('$', 1)[0]
    return _generate_salted_hash(key, text, salt) == hash

def get_secret_key():    
    return config.infobase['secret_key']

def sendmail(to, msg, cc=None):
    cc = cc or []
    if config.get('dummy_sendmail'):
        print 'To:', to
        print 'From:', config.from_address
        print 'Subject:', msg.subject
        print
        print web.safestr(msg)
    else:
        web.sendmail(config.from_address, to, subject=msg.subject.strip(), message=web.safestr(msg), cc=cc)
    
def as_admin(f):
    """Infobase allows some requests only from admin user. This decorator logs in as admin, executes the function and clears the admin credentials."""
    def g(*a, **kw):
        try:
            delegate.admin_login()
            return f(*a, **kw)
        finally:
            web.ctx.headers = []
    return g

@as_admin
def get_user_code(email):
    return web.ctx.site.get_reset_code(email)

@as_admin
def get_user_email(username):
    return web.ctx.site.get_user_email(username).email

@as_admin
def reset_password(username, code, password):
    return web.ctx.site.reset_password(username, code, password)
    
class account(delegate.page):
    @require_login
    def GET(self):
        user = web.ctx.site.get_user()
        return render.account(user)
    
class account_create(delegate.page):
    path = "/account/create"
    
    def GET(self):
        f = forms.Register()
        return render['account/create'](f)
    
    def POST(self):
        i = web.input('email', 'password', 'username')
        i.displayname = i.get('displayname') or i.username
        
        f = forms.Register()
        
        if not f.validates(i):
            return render['account/create'](f)
        
        try:
            web.ctx.site.register(i.username, i.displayname, i.email, i.password)
        except ClientException, e:
            f.note = str(e)
            return render['account/create'](f)
        
        code = _generate_salted_hash(get_secret_key(), i.username + ',' + i.email)
        link = web.ctx.home + "/account/verify?" + urllib.urlencode({'username': i.username, 'email': i.email, 'code': code})
        
        msg = render['email/account/verify'](username=i.username, email=i.email, password=i.password, link=link)
        sendmail(i.email, msg)
        
        return render['account/verify'](username=i.username, email=i.email)
        
del delegate.pages['/account/register']
    
class account_verify(delegate.page):
    path = "/account/verify"
    def GET(self):
        i = web.input(username="", email="", code="")
        verified = _verify_salted_hash(get_secret_key(), i.username + ',' + i.email, i.code)
        
        if verified:
            web.ctx.site.update_user_details(i.username, verified=True)
            return render['account/verify/success'](i.username)
        else:
            return render['account/verify/failed']()

class account_email(delegate.page):
    path = "/account/email"
    
    def get_email(self):
        return context.user.email
        
    @require_login
    def GET(self):
        f = forms.ChangeEmail()
        return render['account/email'](self.get_email(), f)
    
    @require_login
    def POST(self):
        f = forms.ChangeEmail()
        i = web.input()
        
        if not f.validates(i):
            return render['account/email'](self.get_email(), f)
        else:
            username = web.ctx.site.get_user().key.split('/')[-1]
            
            code = _generate_salted_hash(get_secret_key(), username + ',' + i.email)
            link = web.ctx.home + '/account/email/verify' + '?' + urllib.urlencode({"username": username, 'email': i.email, 'code': code})

            msg = render['email/email/verify'](username=username, email=i.email, link=link)
            sendmail(i.email, msg)
            
            title = _("Hi %(user)s", user=username)
            message = _("We've sent an email to %(email)s. You'll need to read that and click on the verification link to update your email.", email=i.email)
            return render.message(title, message)
            
class account_email_verify(delegate.page):
    path = "/account/email/verify"
    
    def GET(self):
        i = web.input(username='', email='', code='')
        
        verified = _verify_salted_hash(get_secret_key(), i.username + ',' + i.email, i.code)
        if verified:
            if web.ctx.site.find_user_by_email(i.email) is not None:
                title = _("Email address is already used.")
                message = _("Your email address couldn't be updated. The specified email address is already used.")
            else:
                web.ctx.site.update_user_details(i.username, email=i.email)
                title = _("Email verification successful.")
                message = _('Your email address has been successfully verified and updated in your account.')
        else:
            title = _("Email address couldn't be verified.")
            message = _("Your email address couldn't be verified. The verification link seems invalid.")
            
        return render.message(title, message)
    
class account_delete(delegate.page):
    path = "/account/delete"
    @require_login
    def GET(self):
        return render['account/delete']()
    
    @require_login
    def POST(self):
        return "Not yet implemented"

class account_password(delegate.page):
    path = "/account/password"

    @require_login
    def GET(self):
        f = forms.ChangePassword()
        return render['account/password'](f)
        
    @require_login
    def POST(self):
        f = forms.ChangePassword()
        i = web.input()
        
        if not f.validates(i):
            return render['account/password'](f)

        try:
            user = web.ctx.site.update_user(i.password, i.new_password, None)
        except ClientException, e:
            f.note = str(e)
            return render['account/password'](f)
            
        add_flash_message('note', _('Your password has been updated successfully.'))
        web.seeother('/')
        
class account_password_forgot(delegate.page):
    path = "/account/password/forgot"

    def GET(self):
        f = forms.ForgotPassword()
        return render['account/password/forgot'](f)
        
    def POST(self):
        i = web.input(email='')
        
        f = forms.ForgotPassword()
        
        if not f.validates(i):
            return render['account/password/forgot'](f)
        
        d = get_user_code(i.email)
        
        link = web.ctx.home + '/account/password/reset' + '?' + urllib.urlencode({'code': d.code, 'username': d.username})
        
        msg = render['email/password/reminder'](d.username, link)
        sendmail(i.email, msg)
        
        return render['account/password/sent'](i.email)

class account_password_reset(delegate.page):
    path = "/account/password/reset"

    def GET(self):
        i = web.input(username='', code='')
    
        try:
            web.ctx.site.check_reset_code(i.username, i.code)
        except ClientException, e:
            title = _("Password reset failed.")
            message = web.safestr(e)
            return render.message(title, message)
            
        f = forms.ResetPassword()
        return render['account/password/reset'](f)
            
    def POST(self):
        i = web.input(username='', code='')

        try:
            web.ctx.site.check_reset_code(i.username, i.code)
        except ClientException, e:
            title = _("Password reset failed.")
            message = web.safestr(e)
            return render.message(title, message)
        
        f = forms.ResetPassword()
        
        if not f.validates(i):
            return render['account/password/reset'](f)
            
        try:
            reset_password(i.username, i.code, i.password)
            web.ctx.site.login(i.username, i.password, False)
            add_flash_message('info', _("Your password has been updated successfully."))
            raise web.seeother('/')
        except Exception, e:
            add_flash_message('error', "Failed to reset password.<br/><br/> Reason: "  + str(e))
            return self.GET()

class account_notifications(delegate.page):
    path = "/account/notifications"
    
    @require_login
    def GET(self):
        prefs = web.ctx.site.get(context.user.key + "/preferences")
        d = (prefs and prefs.get('notifications')) or {}
        email = context.user.email
        return render['account/notifications'](d, email)
        
    @require_login
    def POST(self):
        key = context.user.key + '/preferences'
        prefs = web.ctx.site.get(key)
        
        d = (prefs and prefs.dict()) or {'key': key, 'type': {'key': '/type/object'}}
        
        d['notifications'] = web.input()
        
        web.ctx.site.save(d, 'save notifications')
        
        add_flash_message('note', _("Notification preferences have been updated successfully."))
        web.seeother("/account")

class account_others(delegate.page):
    path = "/account/.*"

    def GET(self):
        return render.notfound(create=False)
        
class redirects(delegate.page):
    path = "/(a|b|user)/(.*)"
    def GET(self, prefix, path):
        d = dict(a="authors", b="books", user="people")
        raise web.redirect("/%s/%s" % (d[prefix], path))
