# This file is to have no project dependencies


def increment_char(c):
    """
    Increment an uppercase character, returning 'A' if 'Z' is given
    """
    return chr(ord(c) + 1) if c != 'Z' else 'A'


def increment_str(s):
    lpart = s.rstrip('Z')
    num_replacements = len(s) - len(lpart)
    new_s = lpart[:-1] + increment_char(lpart[-1]) if lpart else 'A'
    new_s += 'A' * num_replacements
    return new_s


# The following function is based upon code from Jeff Atwood, see:
#
#       https://blog.codinghorror.com/sorting-for-humans-natural-sort-order/
#
# Code has been adapted for for use as sort function for Python sorted(). Enables sorting an 
# iterable whose items are strings represented by a mix of alphanumeric characters. For the
# default sort for {'R14', 'R5'} is:
#
#   R14 R5
#
# but with prep_for_sorting_nicely the sort will be what is more naturally expected:
#
#   R5 R14
#

import re


def prep_for_sorting_nicely(item):
    convert = lambda text: int(text) if text.isdigit() else text
    alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return alphanum_key(item)


# Convert a string with delimited fields into a list of fields. Delimiters are comma,
# semi-colon, colon, tab, or blank space. Fields may contain any printable character.
def listify_string(st):
    ss = re.split(' |:|;|,|\t|\n', st)
    split_st = []
    for s in ss:
        s_strip = s.strip()
        if len(s_strip) != 0:
            split_st.append(s_strip)
    return split_st


# Convert a list of items into a comma-separated string without any surrounding brackets, 
# for example:
#
# list = [1, 2, 3 4]
#
# becomes '1, 2, 3, 4'
#
# as compared to str(list) which
#
# becomes '[1, 2, 3 4]'
def stringify_list(li):
    return ', '.join(str(x) for x in li)


# Check a string reference designator for duplicates as compared to a running set of 
# reference already seen. A reference designator may contain multiple delimited references,
# so need to check the new designator for duplicates before checking against references
# already seen. All duplicate references are added to the set duplicate_refs.
def check_references_for_duplicates(new_refs, seen_refs, duplicate_refs):
    new_refs_list = listify_string(new_refs)
    new_refs_set = set()
    for r in new_refs_list:
        if r in new_refs_set:
            duplicate_refs.add(r)
        else:
            new_refs_set.add(r)
            if r in seen_refs:
                duplicate_refs.add(r)
        seen_refs.add(r)


# Given a string that represents a number, returns a string that eliminates trailing zeros
# and decimal point if any from the input. For example, 25.000 become 25. If the input
# string that does not represent a number then the original string is returned.
def strip_trailing_zeros(num):
    found = False
    for c in num:
        if c.isdigit():
            found = True
        elif c not in ['-', '+', '.']:
            found = False
            break
    return ('%.15f' % float(num)).rstrip('0').rstrip('.') if found else num


# Input a dict with a list of key options, return the value if it exists, else None
def get_from_dict(input_dict, key_options):
    for key in key_options:
        val = input_dict.get(key, None)
        if val:
            return val
    return None