# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2006-2011 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU Lesser General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU Lesser General Public License for more details.
##
## You should have received a copy of the GNU Lesser General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., or visit: http://www.gnu.org/.
##
## Author(s): Stoq Team <stoq-devel@async.com.br>
##
##
"""First time installation wizard for Stoq

Stoq Configuration dialogs

Current flow of the database steps:

> WelcomeStep
> DatabaseLocationStep
if network database:
    > DatabaseSettingsStep
    if has installed db:
        > FinishInstallationStep
        break.
> InstallationModeStep
> PluginStep
if activate tef:
    > TefStep
> AdminPasswordStep
> CreateDatabaseStep
> FinishInstallationStep

"""

import gettext
import os
import socket

import gtk
import gobject
from kiwi.component import provide_utility
from kiwi.datatypes import ValidationError
from kiwi.environ import environ
from kiwi.python import Settable
from kiwi.ui.dialogs import info
from kiwi.ui.delegates import GladeSlaveDelegate
from kiwi.ui.wizard import WizardStep
from stoqlib.exceptions import DatabaseInconsistency
from stoqlib.database.admin import USER_ADMIN_DEFAULT_NAME, ensure_admin_user
from stoqlib.database.database import test_local_database
from stoqlib.database.interfaces import (ICurrentBranchStation,
                                         ICurrentBranch)
from stoqlib.database.runtime import new_transaction
from stoqlib.database.settings import DatabaseSettings
from stoqlib.domain.person import Person
from stoqlib.domain.station import BranchStation
from stoqlib.domain.interfaces import IUser
from stoqlib.domain.system import SystemTable
from stoqlib.exceptions import DatabaseError
from stoqlib.gui.base.wizards import BaseWizard, WizardEditorStep
from stoqlib.gui.slaves.userslave import PasswordEditorSlave
from stoqlib.gui.processview import ProcessView
from stoqlib.lib.message import warning, yesno
from stoqlib.lib.parameters import sysparam
from stoqlib.lib.validators import validate_email
from stoqlib.lib.formatters import raw_phone_number
from stoqlib.lib.webservice import WebService

from stoq.lib.configparser import StoqConfig
from stoq.lib.options import get_option_parser
from stoq.lib.startup import setup, set_default_profile_settings
from stoq.main import run_app

_ = gettext.gettext

LOGO_WIDTH = 91
LOGO_HEIGHT = 32

#
# Wizard Steps
#

(TRUST_AUTHENTICATION,
 PASSWORD_AUTHENTICATION) = range(2)

class BaseWizardStep(WizardStep, GladeSlaveDelegate):
    """A wizard step base class definition"""
    gladefile = None

    def __init__(self, wizard, previous=None):
        self.wizard = wizard
        WizardStep.__init__(self, previous)
        GladeSlaveDelegate.__init__(self, gladefile=self.gladefile)


class WelcomeStep(BaseWizardStep):
    gladefile = "WelcomeStep"

    def __init__(self, wizard):
        BaseWizardStep.__init__(self, wizard)
        self._update_widgets()

    def _update_widgets(self):
        logo_file = environ.find_resource('pixmaps', 'stoq_logo.svg')
        logo = gtk.gdk.pixbuf_new_from_file_at_size(logo_file, LOGO_WIDTH,
                                                    LOGO_HEIGHT)
        self.image1.set_from_pixbuf(logo)
        self.title_label.set_bold(True)

    def next_step(self):
        return DatabaseLocationStep(self.wizard, self)


class DatabaseLocationStep(BaseWizardStep):
    gladefile = 'DatabaseLocationStep'

    def post_init(self):
        self.radio_local.grab_focus()

    def next_step(self):
        self.wizard.db_is_local = self.radio_local.get_active()

        settings = self.wizard.settings
        if self.wizard.db_is_local:
            settings.address = "" # Unix socket really
            # FIXME: Allow developers to specify another database
            #        is_developer_mode() or STOQ_DATABASE_NAME
            settings.dbname = "stoq"
            self.wizard.config.load_settings(self.wizard.settings)

        if (test_local_database() and
            self.wizard.try_connect(settings) and
            self.wizard.has_installed_db):
            return FinishInstallationStep(self.wizard)
        elif self.wizard.db_is_local:
            return InstallationModeStep(self.wizard, self)
        else:
            return DatabaseSettingsStep(self.wizard, self)


class DatabaseSettingsStep(WizardEditorStep):
    gladefile = 'DatabaseSettingsStep'
    model_type = DatabaseSettings
    proxy_widgets = ('address',
                     'port',
                     'username',
                     'password',
                     'dbname')

    def __init__(self, wizard, previous):
        WizardEditorStep.__init__(self, None, wizard, wizard.settings,
                                  previous)
        self._update_widgets()

    def _update_widgets(self):
        selected = self.authentication_type.get_selected_data()
        need_password = selected == PASSWORD_AUTHENTICATION
        self.password.set_sensitive(need_password)
        self.passwd_label.set_sensitive(need_password)

    #
    # WizardStep hooks
    #

    def post_init(self):
        self.register_validate_function(self.wizard.refresh_next)
        self.force_validation()
        self.address.grab_focus()

    def validate_step(self):
        if not self.model.check_database_address():
            msg = _("The database address '%s' is invalid. "
                    "Please fix it and try again"
                    % self.model.address)
            warning(_(u'Invalid database address'), msg)
            # '' is not strictly invalid, since it's an alias for
            # unix socket, so don't tell that to the user, make him
            # belive that he still uses "localhost"
            if self.model.address != "":
                self.address.set_invalid(_("Invalid database address"))
                self.force_validation()
            return False

        settings = self.wizard.settings

        # If we configured setting to localhost, try connecting
        # with address == '', eg unix socket first before trying
        # to connect to localhost. This is done because the default
        # postgres configuration doesn't allow you to connect via localhost,
        # only unix socket.
        if settings.address == 'localhost':
            if not self.wizard.try_connect(settings, warn=False):
                settings.address = ''

        if not self.wizard.try_connect(settings):
            # Restore it
            settings.address = 'localhost'
            return False

        if settings.address == '':
            # Reload settings as they changed
            self.wizard.config.load_settings(settings)

        self.wizard.auth_type = self.authentication_type.get_selected()

        return True

    def setup_proxies(self):
        self.authentication_type.prefill([
            (_("Needs Password"), PASSWORD_AUTHENTICATION),
            (_("Trust"), TRUST_AUTHENTICATION)])

        self.add_proxy(self.model, DatabaseSettingsStep.proxy_widgets)
        # Show localhost instead of empty for unix socket, not strictly
        # correct but better than showing nothing.
        if not self.model.address:
            self.address.set_text("localhost")
        self.model.stoq_user_data = Settable(password='')
        self.add_proxy(self.model.stoq_user_data)

    def next_step(self):
        if self.wizard.has_installed_db:
            return FinishInstallationStep(self.wizard)
        else:
            return InstallationModeStep(self.wizard, self)

    #
    # Callbacks
    #

    def on_authentication_type__content_changed(self, *args):
        self._update_widgets()


class InstallationModeStep(BaseWizardStep):
    gladefile = "InstallationModeStep"
    model_type = object

    def post_init(self):
        self.empty_database_radio.grab_focus()

    def next_step(self):
        self.wizard.enable_production = not self.empty_database_radio.get_active()
        return PluginStep(self.wizard, self)


class PluginStep(BaseWizardStep):
    gladefile = 'PluginStep'

    def post_init(self):
        self.wizard.plugins = []
        self.enable_ecf.grab_focus()

    def next_step(self):
        if self.enable_ecf.get_active():
            self.wizard.plugins.append('ecf')
        if self.enable_nfe.get_active():
            self.wizard.plugins.append('nfe')

        if self.enable_tef.get_active() and not self.wizard.tef_request_done:
            return TefStep(self.wizard, self)
        return AdminPasswordStep(self.wizard, self)


class TefStep(WizardEditorStep):
    """Since we are going to sell the TEF funcionality, we cant enable the
    plugin right away. Just ask for some user information and we will
    contact.
    """
    gladefile = 'TefStep'
    model_type = Settable
    proxy_widgets = ('name', 'email', 'phone')

    def __init__(self, wizard, previous):
        model = Settable(name='', email='', phone='')
        WizardEditorStep.__init__(self, None, wizard, model, previous)
        self._setup_widgets()

    #
    #   Private API
    #

    def _setup_widgets(self):
        self.send_progress.hide()
        self.send_error_label.hide()
        # Setting mask in glade file is not working properly.
        self.phone.set_mask('(00) 0000-0000')

    def _pulse(self):
        self.send_progress.pulse()
        return not self.wizard.tef_request_done

    def _cancel_request(self):
        self._show_error()

    def _show_error(self):
        self.wizard.tef_request_done = True
        self.send_progress.hide()
        self.send_error_label.show()
        self.wizard.next_button.set_sensitive(True)

    #
    #   WizardStep
    #

    def post_init(self):
        self.register_validate_function(self.wizard.refresh_next)
        self.force_validation()
        self.name.grab_focus()

    def setup_proxies(self):
        self.add_proxy(self.model, TefStep.proxy_widgets)

    def next_step(self):
        # We already sent the details, but may still be on the same step.
        if self.wizard.tef_request_done:
            return AdminPasswordStep(self.wizard, self.previous)

        api = WebService()
        response = api.tef_request(self.model.name, self.model.email,
                                   self.model.phone)
        response.ifError(self._on_response_error)
        response.whenDone(self._on_response_done)

        self.send_progress.show()
        self.send_progress.set_text(_('Sending...'))
        self.send_progress.set_pulse_step(0.05)
        self.details_table.set_sensitive(False)
        self.wizard.next_button.set_sensitive(False)
        gobject.timeout_add(50, self._pulse)

        # Cancel the request after 5 seconds without a reply
        gobject.timeout_add(5000, self._cancel_request)

        # Stay on the same step while sending the details
        return self

    #
    #   Callbacks
    #

    def on_email__validate(self, widget, value):
        if not validate_email(value):
            return ValidationError(_('%s is not a valid email') % value)

    def on_phone__validate(self, widget, value):
        if len(raw_phone_number(value)) != 10:
            return ValidationError(_('%s is not a valid phone') % value)

    def _on_response_done(self, response, details):
        if details['response'] != 'success':
            self._show_error()
            return

        if not self.wizard.tef_request_done:
            self.wizard.tef_request_done = True
            self.wizard.go_to_next()

    def _on_response_error(self, response, details):
        self._show_error()


class AdminPasswordStep(BaseWizardStep):
    """ Ask a password for the new user being created. """
    gladefile = 'AdminPasswordStep'

    def __init__(self, wizard, previous):
        BaseWizardStep.__init__(self, wizard, previous)
        self.description_label.set_markup(
            self.get_description_label())
        self.title_label.set_markup(self.get_title_label())
        self.setup_slaves()

    def get_title_label(self):
        return '<b>%s</b>' % _("Administrator account")

    def get_description_label(self):
        return _("I'm adding a user account called <b>%s</b> which will "
                 "have administrator privilegies.\n\nTo be "
                 "able to create other users you need to login "
                 "with this user in the admin application and "
                 "create them.") % USER_ADMIN_DEFAULT_NAME

    def get_slave(self):
        return PasswordEditorSlave(None)

    #
    # WizardStep hooks
    #

    def setup_slaves(self):
        self.password_slave = self.get_slave()
        self.attach_slave("password_holder", self.password_slave)

    def post_init(self):
        self.register_validate_function(self.wizard.refresh_next)
        self.force_validation()
        self.password_slave.password.grab_focus()

    def validate_step(self):
        good_pass = self.password_slave.validate_confirm()
        if good_pass:
            self.wizard.options.login_username = 'admin'
            self.wizard.login_password = self.password_slave.model.new_password
        return good_pass

    def next_step(self):
        if not test_local_database():
            return InstallPostgresStep(self.wizard, self)
        else:
            return CreateDatabaseStep(self.wizard, self)


class InstallPostgresStep(BaseWizardStep):
    """Since we are going to sell the TEF funcionality, we cant enable the
    plugin right away. Just ask for some user information and we will
    contact.
    """
    gladefile = 'InstallPostgresStep'

    def __init__(self, wizard, previous):
        self.done = False
        BaseWizardStep.__init__(self, wizard, previous)
        self._setup_widgets()

    def _setup_widgets(self):
        forward_label = '<b>%s</b>' % (_("Forward"), )

        if self._can_install():
            self.description.props.label += (
                "\n\n" +
                _("The installation guide will now install the packages for you "
                  "using apt, it may ask you for your password to continue."))

            # Translators: %s is the string "Forward"
            label = _("Click %s to begin installing the "
                      "PostgreSQL server.") % (
                forward_label, )
        else:
            # Translators: %s is the string "Forward"
            label = _("Click %s to continue when you have installed "
                      "PostgreSQL server on this machine.") % (
                forward_label, )
        self.label.set_markup(label)

    def _can_install(self):
        try:
            import aptdaemon
            aptdaemon # pyflakes
            return True
        except ImportError:
            return False

    def _install_postgres(self):
        from stoqlib.gui.aptpackageinstaller import AptPackageInstaller
        self.wizard.disable_back()
        self.wizard.disable_next()
        api = AptPackageInstaller(parent=self.wizard.get_toplevel())
        api.install('postgresql')
        api.connect('done', self._on_apt_install__done)

        self.label.set_markup(
            _("Please wait while the package installation is completing."))

    #
    #   WizardStep
    #
    def next_step(self):
        if self.done or test_local_database():
            return CreateDatabaseStep(self.wizard, self)

        self._install_postgres()

        return self

    #
    #   Callbacks
    #

    def _on_apt_install__done(self, api, error):
        if error is not None:
            warning(_("Something went wrong while trying to install "
                      "the PostgreSQL server."))
            self.label.set_markup(
                _("Sorry, something went wrong while installing PostgreSQL, "
                  "try again manually or go back and configure Stoq to connect "
                  "to another."))
            self.wizard.enable_back()
        else:
            self.done = True
            self.wizard.go_to_next()


class CreateDatabaseStep(BaseWizardStep):
    gladefile = 'CreateDatabaseStep'

    def post_init(self):
        self.n_patches = 0
        self.process_view = ProcessView()
        self.process_view.listen_stderr = True
        self.process_view.connect('read-line', self._on_processview__readline)
        self.process_view.connect('finished', self._on_processview__finished)
        self.expander.add(self.process_view)
        self.expander.grab_focus()
        self._maybe_create_database()

    def next_step(self):
        return FinishInstallationStep(self.wizard)

    def _maybe_create_database(self):
        if self.wizard.db_is_local:
            self._launch_stoqdbadmin()
            return
        elif self.wizard.remove_demo:
            self._launch_stoqdbadmin()
            return

        # Save password if using password authentication
        if self.wizard.auth_type == PASSWORD_AUTHENTICATION:
            self._setup_pgpass()
        settings = self.wizard.settings
        self.wizard.config.load_settings(settings)

        conn = settings.get_default_connection()
        version = conn.dbVersion()
        if version < (8, 1):
            info(_("Stoq requires PostgresSQL 8.1 or later, but %s found") %
                 ".".join(map(str, version)))
            conn.close()
            return False

        # Secondly, ask the user if he really wants to create the database,
        dbname = settings.dbname
        if not yesno(_("The specifed database '%s' does not exist.\n"
                       "Do you want to create it?") % dbname,
                     gtk.RESPONSE_NO, _("Don't create"), _("Create database")):
            self.process_view.feed("** Creating database\r\n")
            self._launch_stoqdbadmin()
        else:
            self.process_view.feed("** Not creating database\r\n")

    def _setup_pgpass(self):
        # There's no way to pass in the password to psql, so we need
        # to setup a ~/.pgpass where we store the password entered here
        directory = os.environ.get('HOME', os.environ.get('APPDATA'))
        passfile = os.path.join(directory, '.pgpass')
        pgpass = os.environ.get('PGPASSFILE', passfile)

        if os.path.exists(pgpass):
            lines = []
            for line in open(pgpass):
                if line[-1] == '\n':
                    line = line[:-1]
                lines.append(line)
        else:
            lines = []

        settings = self.wizard.settings
        line = '%s:%s:%s:%s:%s' % (settings.address, settings.port,
                                   settings.dbname,
                                   settings.username, settings.password)
        if line in lines:
            return

        lines.append(line)
        open(pgpass, 'w').write('\n'.join(lines))
        os.chmod(pgpass, 0600)

    def _launch_stoqdbadmin(self):
        self.wizard.disable_back()
        self.wizard.disable_next()
        args = ['stoqdbadmin', 'init',
                '--no-load-config',
                '--no-register-station',
                '-v']
        if self.wizard.enable_production and not self.wizard.remove_demo:
            args.append('--demo')
        if self.wizard.plugins:
            args.append('--enable-plugins')
            args.append(','.join(self.wizard.plugins))
        if self.wizard.db_is_local:
            args.append('--create-dbuser')

        dbargs = self.wizard.settings.get_command_line_arguments()
        args.extend(dbargs)
        self.label.set_label(
            _("Creating a new database for Stoq, depending on the speed of "
              "your computer and the server it may take a couple of "
              "minutes to finish."))
        self.progressbar.set_text(_("Creating database..."))
        self.progressbar.set_fraction(0.05)
        self.process_view.execute_command(args)
        self.done_label.set_markup(
            _("Please wait while the database is being created."))

    def _parse_process_line(self, line):
        LOG_CATEGORY = 'stoqlib.database.create'
        log_pos = line.find(LOG_CATEGORY)
        if log_pos == -1:
            return
        line = line[log_pos+len(LOG_CATEGORY)+1:]
        if line == 'SCHEMA':
            value = 0.1
            text = _("Creating base schema...")
        elif line.startswith('PATCHES:'):
            value = 0.35
            self.n_patches = int(line.split(':', 1)[1])
            text = _("Creating schema, applying patches...")
        elif line.startswith('PATCH:'):
            # 0.4 - 0.7 patches
            patch = float(line.split(':', 1)[1])
            value = 0.4 + (patch / self.n_patches) * 0.3
            text = _("Creating schema, applying patch %d ...") % (patch+1, )
        elif line == 'INIT START':
            text = _("Creating additional database objects ...")
            value = 0.8
        elif line == 'INIT DONE' and self.wizard.enable_production:
            text = _("Creating examples ...")
            value = 0.85
        elif line.startswith('PLUGIN'):
            text = _("Activating plugins ...")
            if 'nfe' in self.wizard.plugins:
                text += ' ' + _('This may take some time.')
            value = 0.95
        else:
            return
        self.progressbar.set_fraction(value)
        self.progressbar.set_text(text)

    def _finish(self, returncode):
        if returncode:
            self.wizard.enable_back()
            # Failed to execute/create database
            if returncode == 30:
                # This probably happened because the user either;
                # - pressed cancel in the authentication popup
                # - user erred the password 3 times
                # Allow him to try again
                if yesno(_("Something went wrong while trying to create "
                           "the database. Try again?"),
                         gtk.RESPONSE_NO, _("Change settings"), _("Try again")):
                    return
                self._launch_stoqdbadmin()
                return
            else:
                # Unknown error, just inform user that something went wrong.
                self.expander.set_expanded(True)
                warning(_("Something went wrong while trying to create "
                          "the Stoq database"))
            return
        self.wizard.load_config_and_call_setup()
        set_default_profile_settings()
        ensure_admin_user(self.wizard.config.get_password())
        self.progressbar.set_text(_("Done."))
        self.progressbar.set_fraction(1.0)
        self.wizard.enable_next()
        self.done_label.set_markup(
            _("Installation successful, click <b>Forward</b> to continue."))

    # Callbacks

    def _on_processview__readline(self, view, line):
        self._parse_process_line(line)

    def _on_processview__finished(self, view, returncode):
        self._finish(returncode)


class FinishInstallationStep(BaseWizardStep):
    gladefile = 'FinishInstallationStep'

    def has_next_step(self):
        return False

    def post_init(self):
        # replaces the cancel button with a quit button
        self.wizard.cancel_button.set_label(gtk.STOCK_QUIT)
        # self._cancel will be a callback for the quit button
        self.wizard.cancel = self._cancel
        self.wizard.next_button.set_label(_(u'Run Stoq'))

    def _cancel(self):
        # This is the last step, so we will finish the installation
        # before we quit
        self.wizard.finish(run=False)


#
# Main wizard
#


class FirstTimeConfigWizard(BaseWizard):
    title = _("Stoq - Installation")
    size = (580, 380)
    tef_request_done = False

    def __init__(self, options, config=None):
        if not config:
            config = StoqConfig()
        self.settings = config.get_settings()

        self.enable_production = False
        self.config = config
        self.remove_demo = False
        self.has_installed_db = False
        self.options = options
        self.plugins = []
        self.db_is_local = False

        if config.get('Database', 'enable_production') == 'True':
            self.remove_demo = True

        if self.remove_demo:
            first_step = PluginStep(self)
        else:
            first_step = WelcomeStep(self)
        BaseWizard.__init__(self, None, first_step, title=self.title)

        self.get_toplevel().set_deletable(False)

    def _create_station(self, trans):
        if self.enable_production:
            branch = sysparam(trans).MAIN_COMPANY
            assert branch
            provide_utility(ICurrentBranch, branch)
        else:
            branch = None
        station = BranchStation(connection=trans,
                                is_active=True,
                                branch=branch,
                                name=socket.gethostname())
        provide_utility(ICurrentBranchStation, station)

    def _set_admin_password(self, trans):
        adminuser = Person.iselectOneBy(IUser,
                                        username=USER_ADMIN_DEFAULT_NAME,
                                        connection=trans)
        if adminuser is None:
            raise DatabaseInconsistency(
                ("You should have a user with username: %s"
                 % USER_ADMIN_DEFAULT_NAME))
        adminuser.password = self.login_password

    def try_connect(self, settings, warn=True):
        try:
            if settings.has_database():
                conn = settings.get_connection()
                self.has_installed_db = SystemTable.is_available(conn)
                conn.close()
        except DatabaseError, e:
            if warn:
                warning(e.short, e.msg)
            return False

        return True


    def load_config_and_call_setup(self):
        dbargs = self.settings.get_command_line_arguments()
        parser = get_option_parser()
        db_options, unused_args = parser.parse_args(dbargs)
        self.config.set_from_options(db_options)
        setup(self.config,
              options=self.options,
              check_schema=True,
              register_station=False,
              load_plugins=True)

    #
    # WizardStep hooks
    #

    def finish(self, run=True):
        if self.has_installed_db:
            self.load_config_and_call_setup()
        else:
            # Commit data created during the wizard, such as stations
            trans = new_transaction()
            self._set_admin_password(trans)
            self._create_station(trans)
            trans.commit()

        # Write configuration to disk
        if self.remove_demo:
            self.config.remove('Database', 'enable_production')
        self.config.flush()

        self.close()
        if run:
            run_app(self.options, 'admin')
