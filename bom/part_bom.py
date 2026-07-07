import logging
from collections import OrderedDict

from djmoney.contrib.exchange.exceptions import MissingRate
from djmoney.contrib.exchange.models import convert_money
from djmoney.money import Money

from .base_classes import AsDictModel

logger = logging.getLogger(__name__)


class PartBom(AsDictModel):
    def __init__(self, part_revision, quantity, unit_cost=None, missing_item_costs=0, nre_cost=None, out_of_pocket_cost=None):
        self.part_revision = part_revision
        self.parts = OrderedDict()
        self.quantity = quantity
        self._currency = self.part_revision.part.organization.currency
        if unit_cost is None:
            unit_cost = Money(0, self._currency)
        if nre_cost is None:
            nre_cost = Money(0, self._currency)
        if out_of_pocket_cost is None:
            out_of_pocket_cost = Money(0, self._currency)

        self.unit_cost = unit_cost
        self.missing_item_costs = missing_item_costs  # count of items that have no cost
        self.nre_cost = nre_cost
        self.out_of_pocket_cost = out_of_pocket_cost  # cost of buying self.quantity with MOQs

    def cost(self):
        return self.unit_cost * self.quantity

    def total_out_of_pocket_cost(self):
        return self.out_of_pocket_cost + self.nre_cost

    def append_item_and_update(self, item):
        if item.bom_id in self.parts:
            self.parts[item.bom_id].extended_quantity += item.extended_quantity
            ref = ', ' + item.references
            self.parts[item.bom_id].references += ref
        else:
            self.parts[item.bom_id] = item

            item.total_extended_quantity = int(self.quantity) * item.extended_quantity
            self.update_bom_for_part(item)

    def update_bom_for_part(self, bom_part):
        if bom_part.do_not_load:
            bom_part.order_quantity = 0
            bom_part.order_cost = Money(0, self._currency)
            return

        if bom_part.seller_part:
            try:
                bom_part.order_quantity = bom_part.seller_part.order_quantity(bom_part.total_extended_quantity)
                raw_order_cost = bom_part.total_extended_quantity * bom_part.seller_part.unit_cost
                bom_part.order_cost = convert_money(raw_order_cost, self._currency)
            except AttributeError:
                pass
            except MissingRate as e:
                logger.error(f"[part_bom.py] Missing exchange rate for order_cost: {e}")
                bom_part.order_cost = Money(0, self._currency)

            if bom_part.seller_part.unit_cost is not None:
                try:
                    converted_unit_cost = convert_money(bom_part.seller_part.unit_cost, self._currency)
                    self.unit_cost = self.unit_cost + (converted_unit_cost * bom_part.extended_quantity)
                except MissingRate as e:
                    logger.error(f"[part_bom.py] Missing exchange rate for unit_cost: {e}")

            self.out_of_pocket_cost = self.out_of_pocket_cost + bom_part.out_of_pocket_cost()

            if bom_part.seller_part.nre_cost is not None:
                try:
                    converted_nre_cost = convert_money(bom_part.seller_part.nre_cost, self._currency)
                    self.nre_cost = self.nre_cost + converted_nre_cost
                except MissingRate as e:
                    logger.error(f"[part_bom.py] Missing exchange rate for nre_cost: {e}")
        else:
            self.missing_item_costs += 1

    def update(self):
        self.missing_item_costs = 0
        self.unit_cost = Money(0, self._currency)
        self.out_of_pocket_cost = Money(0, self._currency)
        self.nre_cost = Money(0, self._currency)
        for _, bom_part in self.parts.items():
            self.update_bom_for_part(bom_part)

    def sourcing_parts(self):
        sourcing_items = {}
        for bom_id, item in self.parts.items():
            if item.part.id not in sourcing_items and item.part.number_class.sourcing_enabled:
                for manufacturer_part in item.part.manufacturer_parts():
                    sourcing_items.update({bom_id: manufacturer_part})
                    if not manufacturer_part.sourcing_disable:
                        sourcing_items.update({bom_id: manufacturer_part})
        return sourcing_items

    def manufacturer_parts(self, source_mouser=False):
        # TODO: optimize this query to not hit the DB in a for loop
        if source_mouser:
            mps = []
            for item in self.parts:
                if item.part.manufacturer_part.source_mouser:
                    mps.append(item.part.manufacturer_part)
            return mps
        return [item.part.manufacturer_part for item in self.parts]

    def as_dict(self, include_id=False):
        d = super().as_dict()
        d['unit_cost'] = self.unit_cost.amount
        d['nre'] = self.nre_cost.amount
        d['out_of_pocket_cost'] = self.out_of_pocket_cost.amount
        return d


class PartBomItem(AsDictModel):
    def __init__(self, bom_id, part, part_revision, do_not_load, references, quantity, extended_quantity, seller_part=None):
        # top_level_quantity is the highest quantity, typically a order quantity for the highest assembly level in a BOM
        # A bom item should not care about its parent quantity
        self.bom_id = bom_id
        self.part = part
        self.part_revision = part_revision
        self.do_not_load = do_not_load
        self.references = references

        self.quantity = quantity  # quantity is the quantity per each direct parent assembly
        self.extended_quantity = extended_quantity  # extended_quantity, is the item quantity used in the top level assembly (e.g. assuming PartBom.quantity = 1)
        self.total_extended_quantity = None  # extended_quantity * top_level_quantity (PartBom.quantity) - Set when appending to PartBom
        self.order_quantity = None  # order quantity taking into MOQ/MPQ constraints - Set when appending to PartBom

        self._currency = self.part.organization.currency

        self.order_cost = Money(0, self._currency)  # order_cost is updated similar to above order_quantity - Set when appending to PartBom
        self.seller_part = seller_part

        self.api_info = None

    def extended_cost(self):
        try:
            raw_cost = self.extended_quantity * self.seller_part.unit_cost
            return convert_money(raw_cost, self._currency)
        except (AttributeError, TypeError) as err:
            logger.log(logging.INFO, '[part_bom.py] ' + str(err))
            return Money(0, self._currency)
        except MissingRate as e:
            logger.error(f"[part_bom.py] Missing exchange rate in extended_cost: {e}")
            return Money(0, self._currency)

    def out_of_pocket_cost(self):
        try:
            raw_cost = self.order_quantity * self.seller_part.unit_cost
            return convert_money(raw_cost, self._currency)
        except (AttributeError, TypeError) as err:
            logger.log(logging.INFO, '[part_bom.py] ' + str(err))
            return Money(0, self._currency)
        except MissingRate as e:
            logger.error(f"[part_bom.py] Missing exchange rate in out_of_pocket_cost: {e}")
            return Money(0, self._currency)

    def as_dict(self, include_id=False):
        dict = super().as_dict()
        del dict['bom_id']
        # The generic serializer stringifies Money/None (so a missing value becomes "None", which is
        # truthy in JS). Emit clean nulls/numerics so the client can reliably tell priced lines from
        # unpriced/non-sourced ones and render the cost columns.
        dict['order_quantity'] = self.order_quantity
        dict['order_cost'] = float(self.order_cost.amount) if self.order_cost is not None else None
        dict['seller_part'] = self.seller_part.as_dict() if self.seller_part is not None else None
        dict['api_info'] = self.api_info if self.api_info else None
        return dict

    def as_dict_for_export(self):
        return {
            'part_number': self.part.full_part_number(),
            'quantity': self.quantity,
            'do_not_load': self.do_not_load,
            'part_class': self.part.number_class.name if self.part.number_class else '',
            'references': self.references,
            'part_synopsis': self.part_revision.synopsis(),
            'part_description': self.part_revision.description,
            'part_revision': self.part_revision.revision,
            'part_manufacturer': self.part.primary_manufacturer_part.manufacturer.name if self.part.primary_manufacturer_part is not None and self.part.primary_manufacturer_part.manufacturer is not None else '',
            'part_manufacturer_part_number': self.part.primary_manufacturer_part.manufacturer_part_number if self.part.primary_manufacturer_part is not None else '',
            'part_ext_qty': self.extended_quantity,
            'part_order_qty': self.order_quantity,
            'part_seller': self.seller_part.seller.name if self.seller_part is not None else '',
            'part_seller_part_number': self.seller_part.seller_part_number if self.seller_part is not None else '',
            'part_cost': self.seller_part.unit_cost.amount if self.seller_part is not None else '',
            'part_moq': self.seller_part.minimum_order_quantity if self.seller_part is not None else 0,
            'part_nre': self.seller_part.nre_cost.amount if self.seller_part is not None else 0,
            'part_ext_cost': self.extended_cost().amount,
            'part_out_of_pocket_cost': self.out_of_pocket_cost().amount,
            'part_lead_time_days': self.seller_part.lead_time_days if self.seller_part is not None else 0,
        }

    def manufacturer_parts_for_export(self):
        return [mp.as_dict_for_export() for mp in self.part.manufacturer_parts(exclude_primary=True)]

    def seller_parts_for_export(self):
        return [sp.as_dict_for_export() for sp in self.part.seller_parts(exclude_primary=True)]

    def __str__(self):
        return f'{self.part.full_part_number()}, qty: {self.quantity}'


class PartIndentedBomItem(PartBomItem, AsDictModel):
    def __init__(self, indent_level, parent_id, subpart, parent_quantity, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.indent_level = indent_level
        self.parent_id = parent_id
        self.subpart = subpart
        self.parent_quantity = parent_quantity

    def as_dict_for_export(self):
        dict = super().as_dict_for_export()
        dict.update({
            'level': self.indent_level,
        })
        return dict

    def __str__(self):
        return f'level: {self.indent_level}, {super().__str__()}'


class WhereUsed(AsDictModel):
    def __init__(self, part_revision=None, part=None):
        self.part_revision = part_revision
        self.part = part
        self.items = OrderedDict()


class WhereUsedItem(AsDictModel):
    def __init__(self, bom_id, part, part_revision, indent_level, parent_id, quantity, references):
        self.bom_id = bom_id
        self.part = part
        self.part_revision = part_revision
        self.indent_level = indent_level
        self.parent_id = parent_id
        self.quantity = quantity
        self.references = references

    def __str__(self):
        return f'level: {self.indent_level}, {self.part.full_part_number()}, qty: {self.quantity}'
