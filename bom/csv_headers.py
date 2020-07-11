from abc import ABC
from .utils import get_from_dict


class CSVHeaderError(Exception):
    def __init__(self, str):
        self.str = str

    def __str__(self):
        return self.str


class CSVHeader:
    def __init__(self, name, *args, **kwargs):
        self.name = name
        self.name_options = kwargs.get('name_options', [])

    def __contains__(self, item):
        if isinstance(item, str):
            return item == self.name
        else:
            return item.name == self.name

    def synonyms(self):
        return [self.name] + self.name_options

    def keys(self):
        return [self.name]

    def __str__(self):
        return self.name


class CSVHeaders(ABC):
    all_headers_defns = []

    def get_synoynms(self, hdr_name):
        for defn in self.all_headers_defns:
            if hdr_name in defn:
                return defn.synonyms()
            else:
                for syn in defn.name_options:
                    if hdr_name == syn:
                        k = defn.keys()
                        return k + defn.name_options

    # If header name does not have a default (i.e., it is not a valid header name) then
    # returns None.
    def get_default(self, hdr_name):
        synonyms = self.get_synoynms(hdr_name)
        return self.get_synoynms(hdr_name)[0] if synonyms is not None else None

    # Preserves order of definitions as listed in all_header_defns:
    def get_default_all(self):
        return [d.name for d in self.all_headers_defns]

    # Given a list of header names returns the default name for each. The return list
    # matches the order of the input list. If a name is not recognized, then returns
    # None as its default:
    def get_defaults_list(self, hdr_names):
        defaults_list = []
        for hdr_name in hdr_names:
            defaults_list.append(self.get_default(hdr_name))
        return defaults_list

    def is_valid(self, hdr_name):
        return self.get_synoynms(hdr_name) is not None

    def get_val_from_row(self, input_dict, hdr_name):
        synonyms = self.get_synoynms(hdr_name)
        return get_from_dict(input_dict, synonyms) if synonyms is not None else None

    def count_matches(self, header, hdr_name):
        c = 0
        syns = self.get_synoynms(hdr_name)
        if syns is not None:
            for h in header:
                c += 1 if h in syns else 0
        return c

    def validate_header_names(self, headers):
        unrecognized_list = []
        for hdr in headers:
            if not self.is_valid(hdr):
                unrecognized_list.append(hdr)
        if len(unrecognized_list) == 1:
            raise CSVHeaderError(("Unrecognized column header \'{}\'").format(unrecognized_list[0]))
        elif len(unrecognized_list) > 1:
            raise CSVHeaderError(("Unrecognized column headers \'{}\'").format(unrecognized_list))

    # Each assertion is expressed in reverse-polish notation with no precendence in order of evaluation.  Operands are
    # header names, operators are:
    #
    #   'in' means contains
    #   'and' means logical AND
    #   'or' means logical OR
    #   'mex' means mutually exclusive (one or the other but not both)
    #
    # For example:
    #   'up', 'down', 'and', 'left', 'or'
    # Means that 'up' and 'down' must be present or just 'left' must be present.
    # All assertions in list must be true or will raise an exception.
    def validate_header_assertions(self, headers, assertion_list):

        def evaluate(headers, operand, operator, prev_count, report):
            c = 0
            if operator == '____count____':
                return self.count_matches(headers, operand)
            elif operator == 'in':
                c = self.count_matches(headers, operand)
                if c == 0:
                    if report: raise CSVHeaderError(("Missing column named \'{}\'").format(operand))
                elif c > 1:
                    if report: raise CSVHeaderError(("Multiple columns with same or synonymous name \'{}\'").format(operand))
            elif operator == 'and':
                c = self.count_matches(headers, operand)
                if c == 0 or prev_count == 0:
                    if report: raise CSVHeaderError(("Missing column named \'{}\'").format(self.get_default(operand)))
            elif operator == 'or':
                c = self.count_matches(headers, operand)
                if c == 0 and prev_count == 0:
                    if report: raise CSVHeaderError(("Missing column named \'{}\'").format(self.get_default(operand)))
            elif operator == 'me':
                c = self.count_matches(headers, operand)
                if c > 1 and prev_count > 1:
                    if report: raise CSVHeaderError(("Conflicting column named \'{}\'").format(self.get_default(operand)))
            return c

        c = 0
        for assertion in assertion_list:
            if (len(assertion) > 1):
                i = 0
                num_asserts = len(assertion)
                while (i < num_asserts):
                    operand = assertion[i]
                    operand_or_operator = assertion[i + 1]
                    if operand_or_operator not in ['in', 'and', 'or', 'mex']:
                        operator = '____count____'
                        c = evaluate(headers, operand, operator, c, i + 1 == num_asserts)
                        i += 1
                    else:
                        operator = operand_or_operator
                        c = evaluate(headers, operand, operator, c, i + 1 == num_asserts)
                        i += 2


#
# For each CSV header class, a static data member dictionary uses the key as the default name for the header while the
# value is the list of synonyms for the header.
#
# In the derive class initialization of the base class member all_headers_defns, the order in which data members are
# listed may be used to prescribe the order in which CVS file columns will be created. So doing depends upon how the
# CSV generation code is written, but as long as it iterates over the list returned by get_default_all() then the
# order will be preserved.
#

class ManufacturerPartCSVHeaders(CSVHeaders):
    all_headers_defns = [
        CSVHeader('manufacturer_name', name_options=['mfg_name', 'manufacturer_name', 'part_manufacturer', 'mfg', 'manufacturer', 'manufacturer name', ]),
        CSVHeader('manufacturer_part_number', name_options=['mpn', 'mfg_part_number', 'part_manufacturer_part_number', 'mfg part number', 'manufacturer part number']),
    ]


class SellerPartCSVHeaders(CSVHeaders):
    all_headers_defns = [
        CSVHeader('seller', name_options=['part_seller', 'part_seller_name', ]),
        CSVHeader('part_cost', name_options=['seller_part_unit_cost', 'unit_cost', ]),
        CSVHeader('moq', name_options=['minimum_order_quantity', 'moq', 'part_moq', ]),
        CSVHeader('nre', name_options=['part_nre', 'part_nre_cost', ]),
    ]


class PartClassesCSVHeaders(CSVHeaders):
    all_headers_defns = [
        CSVHeader('code'),
        CSVHeader('name'),
        CSVHeader('comment', name_options=['description', 'desc', 'desc.']),
        CSVHeader('mouser_enabled'),
    ]


class PartsListCSVHeaders(CSVHeaders):
    part_attributes = [
        CSVHeader('value', name_options=['val', 'val.', ]),
        CSVHeader('value_units', name_options=['value units', 'val. units', 'val units', ]),
        CSVHeader('tolerance', name_options=[]),
        CSVHeader('attribute', name_options=[]),
        CSVHeader('package', name_options=[]),
        CSVHeader('pin_count', name_options=[]),
        CSVHeader('frequency', name_options=[]),
        CSVHeader('frequency_units', name_options=[]),
        CSVHeader('wavelength', name_options=[]),
        CSVHeader('wavelength_units', name_options=[]),
        CSVHeader('memory', name_options=[]),
        CSVHeader('memory_units', name_options=[]),
        CSVHeader('interface', name_options=[]),
        CSVHeader('supply_voltage', name_options=[]),
        CSVHeader('supply_voltage_units', name_options=[]),
        CSVHeader('temperature_rating', name_options=[]),
        CSVHeader('temperature_rating_units', name_options=[]),
        CSVHeader('power_rating', name_options=[]),
        CSVHeader('power_rating_units', name_options=[]),
        CSVHeader('voltage_rating', name_options=[]),
        CSVHeader('voltage_rating_units', name_options=[]),
        CSVHeader('current_rating', name_options=[]),
        CSVHeader('current_rating_units', name_options=[]),
        CSVHeader('material', name_options=[]),
        CSVHeader('color', name_options=[]),
        CSVHeader('finish', name_options=[]),
        CSVHeader('length', name_options=[]),
        CSVHeader('length_units', name_options=[]),
        CSVHeader('width', name_options=[]),
        CSVHeader('width_units', name_options=[]),
        CSVHeader('height', name_options=[]),
        CSVHeader('height_units', name_options=[]),
        CSVHeader('weight', name_options=[]),
        CSVHeader('weight_units', name_options=[]),
    ]

    all_headers_defns = [
        CSVHeader('description', name_options=['desc', 'desc.', ]),
        CSVHeader('manufacturer_name', name_options=['mfg_name', 'manufacturer_name', 'part_manufacturer', 'mfg', 'manufacturer', 'manufacturer name', ]),
        CSVHeader('manufacturer_part_number', name_options=['mpn', 'mfg_part_number', 'part_manufacturer_part_number', 'mfg part number', 'manufacturer part number']),
        CSVHeader('part_number', name_options=['part number', 'part no', ]),
        CSVHeader('revision', name_options=['rev', 'part_revision', ]),
    ] + part_attributes


class PartsListCSVHeadersSemiIntelligent(PartsListCSVHeaders):
    all_headers_defns = [
        CSVHeader('description', name_options=['desc', 'desc.', ]),
        CSVHeader('manufacturer_name', name_options=['mfg_name', 'manufacturer_name', 'part_manufacturer', 'mfg', 'manufacturer', 'manufacturer name', ]),
        CSVHeader('manufacturer_part_number', name_options=['mpn', 'mfg_part_number', 'part_manufacturer_part_number', 'mfg part number', 'manufacturer part number']),
        CSVHeader('part_class', name_options=['class', 'part_category']),
        CSVHeader('part_number', name_options=['part number', 'part no', ]),
        CSVHeader('revision', name_options=['rev', 'part_revision', ]),
    ] + PartsListCSVHeaders.part_attributes


class BOMFlatCSVHeaders(CSVHeaders):
    all_headers_defns = [
        CSVHeader('part_number', name_options=['part number', 'part no', ]),
        CSVHeader('quantity', name_options=['count', 'qty', ]),
        CSVHeader('do_not_load', name_options=['dnl', 'dnp', 'do_not_populate', 'do_not_load', 'do not load', 'do not populate', ]),
        CSVHeader('part_class', name_options=['class', 'part_category']),
        CSVHeader('references', name_options=['designator', 'designators', 'reference', ]),
        CSVHeader('synopsis', name_options=['part_synopsis', ]),
        CSVHeader('revision', name_options=['rev', 'part_revision', 'rev.']),
    ] + ManufacturerPartCSVHeaders.all_headers_defns \
      + SellerPartCSVHeaders.all_headers_defns + [
        CSVHeader('extended_qty', name_options=['extended_quantity', 'part_extended_quantity', 'part_ext_qty', ]),
        CSVHeader('extended_cost', name_options=['part_extended_cost', 'part_ext_cost', ]),
        CSVHeader('order_qty', name_options=['part_order_qty', 'part_order_quantity', 'order_quantity', ]),
        CSVHeader('out_of_pocket_cost', name_options=['part_out_of_pocket_cost', 'cost', ]),
        CSVHeader('lead_time_days', name_options=['part_lead_time_days', ]),
    ]


class BOMIndentedCSVHeaders(BOMFlatCSVHeaders):
    all_headers_defns = [CSVHeader('level')] + BOMFlatCSVHeaders.all_headers_defns
