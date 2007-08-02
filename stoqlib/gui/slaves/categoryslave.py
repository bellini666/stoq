# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2007 Async Open Source <http://www.async.com.br>
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
## Author(s):   George Kussumoto    <george@async.com.br>
##
""" Slaves for categories """

from stoqlib.domain.sellable import SellableCategory
from stoqlib.gui.slaves.sellableslave import TributarySituationSlave
from stoqlib.lib.translation import stoqlib_gettext

_ = stoqlib_gettext

class CategoryTributarySituationSlave(TributarySituationSlave):
    model_type = SellableCategory

    def setup_combos(self):
        constant = self.model.get_tax_constant()
        if constant:
            tax = [(constant.description, constant)]
        else:
            tax = [(_('No tax'), None)]

        self.tax_constant.prefill(tax)
