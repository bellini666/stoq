# -*- coding: utf-8 -*-
# vi:si:et:sw=4:sts=4:ts=4

##
## Copyright (C) 2007-2009 Async Open Source <http://www.async.com.br>
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
""" Stock transfer wizard definition """

import datetime
from decimal import Decimal

import gtk
from kiwi.datatypes import ValidationError
from kiwi.python import Settable
from kiwi.ui.widgets.list import Column

from stoqlib.database.runtime import get_current_branch
from stoqlib.domain.interfaces import IBranch, IEmployee, IStorable
from stoqlib.domain.person import Person
from stoqlib.domain.sellable import Sellable
from stoqlib.domain.transfer import TransferOrder, TransferOrderItem
from stoqlib.gui.base.columns import AccessorColumn
from stoqlib.gui.base.wizards import (BaseWizard, BaseWizardStep)
from stoqlib.gui.printing import print_report
from stoqlib.gui.wizards.abstractwizard import SellableItemStep
from stoqlib.lib.message import yesno
from stoqlib.lib.translation import stoqlib_gettext
from stoqlib.lib.validators import format_quantity
from stoqlib.reporting.transfer_receipt import TransferOrderReceipt

_ = stoqlib_gettext


#
# Wizard steps
#

class TemporaryTransferOrder(object):

    def __init__(self):
        self.items = []
        self.open_date = datetime.date.today()
        self.receival_date = datetime.date.today()
        self.source_branch = None
        self.destination_branch = None
        self.source_responsible = None
        self.destination_responsible = None

    def add_item(self, item):
        self.items.append(item)

    def get_items(self):
        return self.items

    def remove_item(self, item):
        self.items.remove(item)


class TemporaryTransferOrderItem(Settable):
    pass


class StockTransferProductStep(SellableItemStep):
    model_type = TemporaryTransferOrder
    item_table = TemporaryTransferOrderItem

    def __init__(self, wizard, conn, model):
       self.branch = get_current_branch(conn)
       SellableItemStep.__init__(self, wizard, None, conn, model)

    #
    # SellableItemStep hooks
    #

    def setup_sellable_entry(self):
        branch = get_current_branch(self.conn)
        sellables = Sellable.get_unblocked_sellables(self.conn,
                                                     storable=True)
        self.sellable.prefill([(s.get_description(), s) for s in sellables
                                if IStorable(s.product).has_stock_by_branch(self.branch)])

    def get_saved_items(self):
        return list(self.model.get_items())

    def get_order_item(self, sellable, cost, quantity):
        item = self.get_model_item_by_sellable(sellable)
        if item is not None:
            item.quantity += quantity
        else:
            item = TemporaryTransferOrderItem(quantity=quantity,
                                              sellable=sellable)
            self.model.add_item(item)
        return item

    def get_columns(self):
        return [
            Column('sellable.description', title=_(u'Description'),
                   data_type=str, expand=True, searchable=True),
            AccessorColumn('stock', title=_(u'Stock'), data_type=Decimal,
                           accessor=self._get_stock_quantity, width=80),
            Column('quantity', title=_(u'Transfer'), data_type=Decimal,
                   format_func=format_quantity, width=100),
            AccessorColumn('total', title=_(u'Total'), data_type=Decimal,
                            accessor=self._get_total_quantity, width=80),
            ]

    def _get_stock_quantity(self, item):
        storable = IStorable(item.sellable.product)
        stock_item = storable.get_stock_item(self.branch)
        return stock_item.quantity or 0

    def _get_total_quantity(self, item):
        qty = self._get_stock_quantity(item)
        qty -= item.quantity
        if qty > 0:
            return qty
        return 0

    def _setup_summary(self):
        self.summary = None

    def _get_stock_balance(self, sellable):
        storable = IStorable(sellable.product)
        quantity = storable.get_full_balance(self.branch) or Decimal(0)
        # do not count the added quantity
        for item in self.slave.klist:
            if item.sellable == sellable:
                quantity -= item.quantity
                break

        return quantity

    def sellable_selected(self, sellable):
        SellableItemStep.sellable_selected(self, sellable)

        if sellable is None:
            return

        quantity = self._get_stock_balance(sellable)
        has_quantity = quantity > 0
        self.quantity.set_sensitive(has_quantity)
        self.cost.set_sensitive(has_quantity)
        self.add_sellable_button.set_sensitive(has_quantity)
        if has_quantity:
            self.quantity.set_range(1, quantity)

    def setup_slaves(self):
        SellableItemStep.setup_slaves(self)
        self.quantity.connect('validate', self._on_quantity__validate)

    #
    # WizardStep hooks
    #

    def post_init(self):
        self.product_button.hide()
        self.hide_add_button()
        self.hide_edit_button()

    def next_step(self):
        return StockTransferFinishStep(self.conn, self.wizard,
                                       self.model, self)

    def _on_quantity__validate(self, widget, value):
        if not value or value <= 0:
            return ValidationError(_(u'Quantity should be a positive number.'))

        sellable = self.sellable.get_selected()
        balance = self._get_stock_balance(sellable)
        if value > balance:
            return ValidationError(
                _(u'Quantity is greater than the quantity in stock.'))


class StockTransferFinishStep(BaseWizardStep):
    gladefile = 'StockTransferFinishStep'
    proxy_widgets = ('open_date',
                     'receival_date',
                     'destination_responsible',
                     'destination_branch',
                     'source_responsible')

    def __init__(self, conn, wizard, transfer_order, previous):
        self.conn = conn
        self.transfer_order = transfer_order
        self.branch = get_current_branch(self.conn)
        BaseWizardStep.__init__(self, self.conn, wizard, previous)
        self.setup_proxies()

    def setup_proxies(self):
        self._setup_widgets()
        self.proxy = self.add_proxy(self.transfer_order,
                                    StockTransferFinishStep.proxy_widgets)

    def _setup_widgets(self):
        branches = [(b.person.name, b)
                    for b in Person.iselect(IBranch, connection=self.conn)
                                        if b is not self.branch]
        self.destination_branch.prefill(branches)
        self.source_branch.set_text(self.branch.person.name)
        employees = [(e.person.name, e)
                     for e in Person.iselect(IEmployee,
                                             connection=self.conn)]
        self.source_responsible.prefill(employees)
        self.destination_responsible.prefill(employees)

    #
    # WizardStep hooks
    #

    def post_init(self):
        self.register_validate_function(self.wizard.refresh_next)
        self.force_validation()

    def has_previous_step(self):
        return True

    def has_next_step(self):
        return False

    def validate_step(self):
        # since this is the last step, this method is used like a
        # callback for the finish button
        dest_branch = self.proxy.model.destination_branch
        assert dest_branch is not None

        self.transfer_order.source_branch = self.branch
        return True

    #
    # Kiwi callbacks
    #

    def on_open_date__validate(self, widget, date):
        if date < datetime.date.today():
            return ValidationError(_(u"The date must be set to today "
                                      "or a future date"))
        receival_date = self.receival_date.get_date()
        if receival_date is not None and date > receival_date:
            return ValidationError(_(u"The open date must be set to "
                                      "before the receival date"))

    def on_receival_date__validate(self, widget, date):
        open_date = self.open_date.get_date()
        if open_date > date:
            return ValidationError(_(u"The receival date must be set "
                                      "to after the open date"))

    def on_destination_branch__content_changed(self, *args):
        dest_branch = self.destination_branch.get_selected()
        self.transfer_order.destination_branch = dest_branch


#
# Main wizard
#


class StockTransferWizard(BaseWizard):
    title = _(u'Stock Transfer')
    size = (750, 350)

    def __init__(self, conn):
        self.model = TemporaryTransferOrder()
        first_step = StockTransferProductStep(self, conn, self.model)
        BaseWizard.__init__(self, conn, first_step, self.model)
        self.next_button.set_sensitive(False)

    def _receipt_dialog(self, order):
        msg = _(u'Would you like to print a receipt for this transfer?')
        if yesno(msg, gtk.RESPONSE_YES, _(u'Yes'), _('No')):
           items = TransferOrderItem.selectBy(transfer_order=order,
                                              connection=self.conn)
           print_report(TransferOrderReceipt, order, items)
        return

    def finish(self):
        order = TransferOrder(
            open_date=self.model.open_date,
            receival_date=self.model.receival_date,
            source_branch=self.model.source_branch,
            destination_branch=self.model.destination_branch,
            source_responsible=self.model.source_responsible,
            destination_responsible=self.model.destination_responsible,
            connection=self.conn)
        for item in self.model.get_items():
            transfer_item = TransferOrderItem(connection=self.conn,
                                              transfer_order=order,
                                              sellable=item.sellable,
                                              quantity=item.quantity)
            order.send_item(transfer_item)
        #XXX Waiting for transfer order receiving wizard implementation
        order.receive()

        self.retval = self.model
        self.close()
        self._receipt_dialog(order)
