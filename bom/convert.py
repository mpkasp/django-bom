from .models import PartClass


def full_part_number_to_broken_part(part_number):
    part_class = PartClass.objects.filter(code=part_number[:3])[0]
    part_item = part_number[4:8]
    part_variation = part_number[9:]

    civ = {
        'class': part_class,
        'item': part_item,
        'variation': part_variation
    }

    return civ
