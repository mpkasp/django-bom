from .models import Part, PartClass, Manufacturer, ManufacturerPart, Subpart, Seller, SellerPart, User, UserMeta, \
    Organization, PartRevision, AssemblySubparts, Assembly
from .utils import get_from_dict


class CSVHeaderError(Exception):
    def __init__(self, str):
        self.str = str

    def __str__(self):
        return self.str


class CSVHeaders:

    def __init__(self):
        self.all_header_defns = []

    # Returns a list of synonyms or None if there are no synonyms.
    def get_synoynms(self, hdr_name):
        for defn in self.all_headers_defns:
            if hdr_name in defn:
                return [hdr_name] + list(defn.values())[0]
            else:
                for syns in defn.values():
                    for syn in syns:
                        if hdr_name == syn:
                            k = list(defn.keys())[0]
                            return [k] + list(defn.values())[0]

    # If header name does not have a default (i.e., it is not a valid header name) then
    # returns None.
    def get_default(self, hdr_name):
        synonyms = self.get_synoynms(hdr_name)
        return self.get_synoynms(hdr_name)[0] if synonyms is not None else None

    # Preserves order of definitions as listed in all_header_defns:
    def get_default_all(self):
        all_defaults = []
        for defn in self.all_headers_defns:
            all_defaults.append(list(defn.keys())[0])
        return all_defaults

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


class PartClassesCSVHeaders(CSVHeaders):
    code = {'code': []}
    comment = {'comment': ['description', 'desc', 'desc.']}
    mouser_enabled = {'mouser_enabled': []}
    name = {'name': []}

    def __init__(self):
        super().__init__()
        self.all_headers_defns = [
            PartClassesCSVHeaders.code,
            PartClassesCSVHeaders.name,
            PartClassesCSVHeaders.comment,
            PartClassesCSVHeaders.mouser_enabled,
        ]


class PartsListCSVHeaders(CSVHeaders):
    description = {'description': ['desc', 'desc.', ]}
    mfg_name = {'manufacturer_name': ['mfg_name', 'manufacturer_name', 'part_manufacturer', 'mfg', 'manufacturer', 'manufacturer name', ]}
    mpn = {'manufacturer_part_number': ['mpn', 'mfg_part_number', 'part_manufacturer_part_number', 'mfg part number', 'manufacturer part number']}
    part_class = {'part_class': ['class', 'part_category']}
    part_number = {'part_number': ['part number', 'part no', ]}
    revision = {'revision': ['rev', 'part_revision', ]}
    value = {'value': ['val', 'val.', ]}
    value_units = {'value_units': ['value units', 'val. units', 'val units', ]}
    tolerance = {'tolerance': []}
    attribute = {'attribute': []}
    package = {'package': []}
    pin_count = {'pin_count': []}
    frequency = {'frequency': []}
    frequency_units = {'frequency_units': []}
    wavelength = {'wavelength': []}
    wavelength_units = {'wavelength_units': []}
    memory = {'memory': []}
    memory_units = {'memory_units': []}
    interface = {'interface': []}
    supply_voltage = {'supply_voltage': []}
    supply_voltage_units = {'supply_voltage_units': []}
    temperature_rating = {'temperature_rating': []}
    temperature_rating_units = {'temperature_rating_units': []}
    power_rating = {'power_rating': []}
    power_rating_units = {'power_rating_units': []}
    voltage_rating = {'voltage_rating': []}
    voltage_rating_units = {'voltage_rating_units': []}
    current_rating = {'current_rating': []}
    current_rating_units = {'current_rating_units': []}
    material = {'material': []}
    color = {'color': []}
    finish = {'finish': []}
    length = {'length': []}
    length_units = {'length_units': []}
    width = {'width': []}
    width_units = {'width_units': []}
    height = {'height': []}
    height_units = {'height_units': []}
    weight = {'weight': []}
    weight_units = {'weight_units': []}

    def __init__(self):
        super().__init__()
        self.all_headers_defns = [
            PartsListCSVHeaders.description,
            PartsListCSVHeaders.mfg_name,
            PartsListCSVHeaders.mpn,
            PartsListCSVHeaders.part_class,
            PartsListCSVHeaders.part_number,
            PartsListCSVHeaders.revision,
            PartsListCSVHeaders.value,
            PartsListCSVHeaders.value_units,
            PartsListCSVHeaders.tolerance,
            PartsListCSVHeaders.attribute,
            PartsListCSVHeaders.package,
            PartsListCSVHeaders.pin_count,
            PartsListCSVHeaders.frequency,
            PartsListCSVHeaders.frequency_units,
            PartsListCSVHeaders.wavelength,
            PartsListCSVHeaders.wavelength_units,
            PartsListCSVHeaders.memory,
            PartsListCSVHeaders.memory_units,
            PartsListCSVHeaders.interface,
            PartsListCSVHeaders.supply_voltage,
            PartsListCSVHeaders.supply_voltage_units,
            PartsListCSVHeaders.temperature_rating,
            PartsListCSVHeaders.temperature_rating_units,
            PartsListCSVHeaders.power_rating,
            PartsListCSVHeaders.power_rating_units,
            PartsListCSVHeaders.voltage_rating,
            PartsListCSVHeaders.voltage_rating_units,
            PartsListCSVHeaders.current_rating,
            PartsListCSVHeaders.current_rating_units,
            PartsListCSVHeaders.material,
            PartsListCSVHeaders.color,
            PartsListCSVHeaders.finish,
            PartsListCSVHeaders.length,
            PartsListCSVHeaders.length_units,
            PartsListCSVHeaders.width,
            PartsListCSVHeaders.width_units,
            PartsListCSVHeaders.height,
            PartsListCSVHeaders.height_units,
            PartsListCSVHeaders.weight,
            PartsListCSVHeaders.weight_units,
        ]


class BOMFlatCSVHeaders(CSVHeaders):
    do_not_load = {'do_not_load': ['dnl', 'dnp', 'do_not_populate', 'do_not_load', 'do not load', 'do not populate', ]}
    part_class = {'part_class': ['class', 'part_category']}
    part_cost = {'part_cost': ['seller_part_unit_cost', 'unit_cost', ]}
    part_ext_cost = {'extended_cost': ['part_extended_cost', 'part_ext_cost', ]}
    part_ext_qty = {'extended_qty': ['extended_quantity', 'part_extended_quantity', 'part_ext_qty', ]}
    part_lead_time_days = {'lead_time_days': ['part_lead_time_days', ]}
    part_manufacturer = {'manufacturer_name': ['mfg_name', 'manufacturer_name', 'part_manufacturer', 'mfg', 'manufacturer', 'manufacturer name', ]}
    part_manufacturer_part_number = {'manufacturer_part_number': ['mpn', 'mfg_part_number', 'part_manufacturer_part_number', 'mfg part number', 'manufacturer part number']}
    part_moq = {'moq': ['minimum_order_quantity', 'moq', 'part_moq', ]}
    part_nre = {'nre': ['part_nre', 'part_nre_cost', ]}
    part_number = {'part_number': ['part number', 'part no', ]}
    part_order_qty = {'order_qty': ['part_order_qty', 'part_order_quantity', 'order_quantity', ]}
    part_out_of_pocket_cost = {'out_of_pocket_cost': ['part_out_of_pocket_cost', 'cost', ]}
    part_revision = {'revision': ['rev', 'part_revision', ]}
    part_seller = {'seller': ['part_seller', 'part_seller_name', ]}
    part_synopsis = {'synopsis': ['part_synopsis', ]}
    quantity = {'quantity': ['count', 'qty', ]}
    references = {'references': ['designator', 'designators', 'reference', ]}

    def __init__(self):
        super().__init__()
        self.all_headers_defns = [
            BOMFlatCSVHeaders.part_number,
            BOMFlatCSVHeaders.quantity,
            BOMFlatCSVHeaders.do_not_load,
            BOMFlatCSVHeaders.part_class,
            BOMFlatCSVHeaders.references,
            BOMFlatCSVHeaders.part_synopsis,
            BOMFlatCSVHeaders.part_revision,
            BOMFlatCSVHeaders.part_manufacturer,
            BOMFlatCSVHeaders.part_manufacturer_part_number,
            BOMFlatCSVHeaders.part_ext_qty,
            BOMFlatCSVHeaders.part_ext_cost,
            BOMFlatCSVHeaders.part_order_qty,
            BOMFlatCSVHeaders.part_seller,
            BOMFlatCSVHeaders.part_cost,
            BOMFlatCSVHeaders.part_moq,
            BOMFlatCSVHeaders.part_nre,
            BOMFlatCSVHeaders.part_out_of_pocket_cost,
            BOMFlatCSVHeaders.part_lead_time_days,
        ]


class BOMIndentedCSVHeaders(BOMFlatCSVHeaders):
    level = {'level': []}

    def __init__(self):
        super().__init__()
        self.all_headers_defns.append(BOMIndentedCSVHeaders.level)
