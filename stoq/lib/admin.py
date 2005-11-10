#!/usr/bin/env python
# -*- Mode: Python; coding: iso-8859-1 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2005 Async Open Source <http://www.async.com.br>
## All rights reserved
##
## This program is free software; you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation; either version 2 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program; if not, write to the Free Software
## Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307,
## USA.
##
"""
stoq/lib/admin.py:

Helper functions related to administration of the database, creating
tables, removing tables and configuring administration user.
"""

import sys

from stoq.lib.runtime import new_transaction, print_msg
from stoq.lib.parameters import sysparam
from stoq.domain.person import EmployeePosition, PersonAdaptToUser
from stoq.domain.profile import UserProfile
from stoq.domain.tables import get_table_types
from stoq.domain.interfaces import (IIndividual, IEmployee, IUser,
                                    ISalesPerson)

def ensure_admin_user(name, username, password):
    print_msg("Creating administrator user...", break_line=False)
    trans = new_transaction()

    # XXX Person for administrator user is the same of Current Branch. I'm not 
    # sure if it's the best approach but for sure it's better than 
    # create another one just for this user.
    company = sysparam(trans).CURRENT_BRANCH
    person_obj = company.get_adapted()

    # Dependencies to create an user.
    position = EmployeePosition(name='Administrator position', connection=trans)
    user = person_obj.addFacet(IIndividual, connection=trans)
    user = person_obj.addFacet(IEmployee, position=position,
                               connection=trans)
    # This is usefull when testing a initial database. Admin user actually
    # must have all the facets.
    person_obj.addFacet(ISalesPerson, connection=trans)

    profile = UserProfile.create_profile_template(trans, 'Administrator',
                                                  has_full_permission=True)
    
    user = person_obj.addFacet(IUser, username=username, password=password,
                               profile=profile, connection=trans)
    catalog = PersonAdaptToUser
    ret = catalog.select(catalog.q.username == 'administrator',
                         connection=trans)
    assert ret, ret.count() == 1
    assert ret[0].password == password
    trans.commit()
    print_msg('done')
    return user

def setup_tables(delete_only=False, list_tables=False, verbose=False):
    if not list_tables and verbose:
        print_msg('Setting up tables... ', break_line=False)
    else:
        print_msg('Setting up tables... ')

    catalog_types = get_table_types()
    trans = new_transaction()
    for catalog in catalog_types:
        if trans.tableExists(catalog.get_db_table_name()):
            catalog.dropTable(ifExists=True, cascade=True, connection=trans)
            if list_tables:
                print_msg('<removed>:  %s' % catalog)
        if delete_only:
            continue
        catalog.createTable(connection=trans)
        if list_tables:
            print_msg('<created>:  %s' % catalog)

    trans.commit()
    sys.stdout.flush()
