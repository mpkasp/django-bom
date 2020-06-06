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
    if st is None:
        return []
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
# list = [1, 2, 3, 4]
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
    return ('%f' % float(num)).rstrip('0').rstrip('.') if found else num


# Input a dict with a list of key options, return the value if it exists, else None
def get_from_dict(input_dict, key_options):
    for key in key_options:
        val = input_dict.get(key, None)
        if val:
            return val
    return None

# via https://github.com/hayj/SystemTools/blob/master/systemtools/number.py
def parse_number(text):
    """
        Return the first number in the given text for any locale.
        TODO we actually don't take into account spaces for only
        3-digited numbers (like "1 000") so, for now, "1 0" is 10.
        TODO parse cases like "125,000.1,0.2" (125000.1).
        :example:
        >>> parseNumber("a 125,00 €")
        125
        >>> parseNumber("100.000,000")
        100000
        >>> parseNumber("100 000,000")
        100000
        >>> parseNumber("100,000,000")
        100000000
        >>> parseNumber("100 000 000")
        100000000
        >>> parseNumber("100.001 001")
        100.001
        >>> parseNumber("$.3")
        0.3
        >>> parseNumber(".003")
        0.003
        >>> parseNumber(".003 55")
        0.003
        >>> parseNumber("3 005")
        3005
        >>> parseNumber("1.190,00 €")
        1190
        >>> parseNumber("1190,00 €")
        1190
        >>> parseNumber("1,190.00 €")
        1190
        >>> parseNumber("$1190.00")
        1190
        >>> parseNumber("$1 190.99")
        1190.99
        >>> parseNumber("$-1 190.99")
        -1190.99
        >>> parseNumber("1 000 000.3")
        1000000.3
        >>> parseNumber('-151.744122')
        -151.744122
        >>> parseNumber('-1')
        -1
        >>> parseNumber("1 0002,1.2")
        10002.1
        >>> parseNumber("")
        >>> parseNumber(None)
        >>> parseNumber(1)
        1
        >>> parseNumber(1.1)
        1.1
        >>> parseNumber("rrr1,.2o")
        1
        >>> parseNumber("rrr1rrr")
        1
        >>> parseNumber("rrr ,.o")
    """
    try:
        # First we return None if we don't have something in the text:
        if text is None:
            return None
        if isinstance(text, int) or isinstance(text, float):
            return text
        text = text.strip()
        if text == "":
            return None
        # Next we get the first "[0-9,. ]+":
        n = re.search("-?[0-9]*([,. ]?[0-9]+)+", text).group(0)
        n = n.strip()
        if not re.match(".*[0-9]+.*", text):
            return None
        # Then we cut to keep only 2 symbols:
        while " " in n and "," in n and "." in n:
            index = max(n.rfind(','), n.rfind(' '), n.rfind('.'))
            n = n[0:index]
        n = n.strip()
        # We count the number of symbols:
        symbolsCount = 0
        for current in [" ", ",", "."]:
            if current in n:
                symbolsCount += 1
        # If we don't have any symbol, we do nothing:
        if symbolsCount == 0:
            pass
        # With one symbol:
        elif symbolsCount == 1:
            # If this is a space, we just remove all:
            if " " in n:
                n = n.replace(" ", "")
            # Else we set it as a "." if one occurence, or remove it:
            else:
                theSymbol = "," if "," in n else "."
                if n.count(theSymbol) > 1:
                    n = n.replace(theSymbol, "")
                else:
                    n = n.replace(theSymbol, ".")
        else:
            # Now replace symbols so the right symbol is "." and all left are "":
            rightSymbolIndex = max(n.rfind(','), n.rfind(' '), n.rfind('.'))
            rightSymbol = n[rightSymbolIndex:rightSymbolIndex+1]
            if rightSymbol == " ":
                return parseNumber(n.replace(" ", "_"))
            n = n.replace(rightSymbol, "R")
            leftSymbolIndex = max(n.rfind(','), n.rfind(' '), n.rfind('.'))
            leftSymbol = n[leftSymbolIndex:leftSymbolIndex+1]
            n = n.replace(leftSymbol, "L")
            n = n.replace("L", "")
            n = n.replace("R", ".")
        # And we cast the text to float or int:
        n = float(n)
        if n.is_integer():
            return int(n)
        else:
            return n
    except: pass
    return None