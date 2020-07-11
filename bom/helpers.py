from bom.models import Part, PartClass, Seller, SellerPart, Subpart, \
    Manufacturer, Organization, ManufacturerPart, PartRevision, Assembly, User, AssemblySubparts
from bom import constants


def create_a_fake_organization(user, free=False, number_scheme=constants.NUMBER_SCHEME_SEMI_INTELLIGENT, number_variation_len=constants.NUMBER_VARIATION_LEN_DEFAULT):
    org, created = Organization.objects.get_or_create(
        name="Atlas",
        subscription=constants.SUBSCRIPTION_TYPE_FREE if free else constants.SUBSCRIPTION_TYPE_PRO,
        number_scheme=number_scheme,
        number_item_len=4,
        number_variation_len=number_variation_len,
        owner=user)
    return org


def create_user_and_organization(free=False, number_scheme=constants.NUMBER_SCHEME_SEMI_INTELLIGENT, number_variation_len=constants.NUMBER_VARIATION_LEN_DEFAULT):
    user = User.objects.create_user('kasper', 'kasper@McFadden.com', 'ghostpassword')
    organization = create_a_fake_organization(user, free, number_scheme, number_variation_len)
    profile = user.bom_profile(organization=organization)
    profile.role = 'A'
    profile.save()
    return user, organization


def create_some_fake_part_classes(organization):
    pc1, c = PartClass.objects.get_or_create(code=500, name='Wendy', comment='Mechanical Switches', organization=organization)
    pc2, c = PartClass.objects.get_or_create(code=200, name='Archibald', comment='', organization=organization)
    pc3, c = PartClass.objects.get_or_create(code='50A', name='Ghost', comment='Like Kasper', organization=organization)
    return pc1, pc2, pc3


def create_a_fake_subpart(part_revision, reference="U1", count=4):
    sp1 = Subpart(
        part_revision=part_revision,
        reference=reference,
        count=count)
    sp1.save()

    return sp1


def create_a_fake_assembly():
    assy = Assembly.objects.create()
    return assy


def create_a_fake_assembly_with_subpart(part_revision, reference="D4", count=4):
    assy = create_a_fake_assembly()
    subpart = create_a_fake_subpart(part_revision, reference, count)
    assy.subparts.add(subpart)
    return assy


def create_a_fake_part_revision(part, assembly, description="Brown dog", revision="1"):
    pch, created = PartRevision.objects.get_or_create(part=part, revision=revision, defaults={
        'description': description,
        'revision': revision,
        'attribute': "Voltage",
        'value': "3.3",
        'assembly': assembly,
    })
    return pch


def create_some_fake_sellers(organization):
    s1, c = Seller.objects.get_or_create(name='Mouser', organization=organization)
    s2, c = Seller.objects.get_or_create(name='Digi-Key', organization=organization)
    s3, c = Seller.objects.get_or_create(name='Archibald', organization=organization)
    return s1, s2, s3


def create_some_fake_manufacturers(organization):
    m1, c = Manufacturer.objects.get_or_create(name='STMicroelectronics', organization=organization)
    m2, c = Manufacturer.objects.get_or_create(name='Nordic Semiconductor', organization=organization)
    m3, c = Manufacturer.objects.get_or_create(name='Murata', organization=organization)
    return m1, m2, m3


def create_a_fake_seller_part(
        seller,
        manufacturer_part,
        moq,
        mpq,
        unit_cost,
        lead_time_days,
        nre_cost=None):
    sp1 = SellerPart(
        seller=seller,
        manufacturer_part=manufacturer_part,
        minimum_order_quantity=moq,
        minimum_pack_quantity=mpq,
        unit_cost=unit_cost,
        lead_time_days=lead_time_days,
        nre_cost=nre_cost)
    sp1.save()

    return sp1


def create_some_fake_intelligent_parts(organization):
    pt1 = Part(number_item=('3' * organization.number_item_len), organization=organization)
    pt1.save()

    pt2 = Part(number_item='4' * organization.number_item_len, organization=organization)
    pt2.save()

    pt3 = Part(number_item='A' * organization.number_item_len, organization=organization)
    pt3.save()

    # pt4 is a part with no PartRevision
    pt4 = Part(number_item='B' * organization.number_item_len, organization=organization)
    pt4.save(no_part_revision=True)

    pt5 = Part(number_item='5' * organization.number_item_len, organization=organization)
    pt5.save()
    return pt1, pt2, pt3, pt4, pt5


def create_some_fake_semi_intelligent_parts(organization):
    (pc1, pc2, pc3) = create_some_fake_part_classes(organization=organization)
    pt1 = Part(number_class=pc2, number_item='3333', organization=organization)
    pt1.save()

    pt2 = Part(number_class=pc1, organization=organization)
    pt2.save()

    pt3 = Part(number_class=pc3, organization=organization)
    pt3.save()

    # pt4 is a part with no PartRevision
    pt4 = Part(number_class=pc1, number_item='4444', organization=organization)
    pt4.save(no_part_revision=True)

    pt5 = Part(number_class=pc1, number_item='5555', organization=organization)
    pt5.save()
    return pt1, pt2, pt3, pt4, pt5


def create_some_fake_parts(organization):
    (m1, m2, m3) = create_some_fake_manufacturers(organization=organization)

    if organization.number_scheme == 'I':
        pt1, pt2, pt3, pt4, pt5 = create_some_fake_intelligent_parts(organization)
    elif organization.number_scheme == 'S':
        pt1, pt2, pt3, pt4, pt5 = create_some_fake_semi_intelligent_parts(organization)
    else:
        return None

    mp1 = ManufacturerPart(part=pt1, manufacturer=m1, manufacturer_part_number='STM32F401CEU6')
    mp1.save()
    pt1.primary_manufacturer_part = mp1
    pt1.save()
    assy = create_a_fake_assembly()
    pr1 = create_a_fake_part_revision(part=pt1, assembly=None)

    mp2 = ManufacturerPart(part=pt2, manufacturer=None, manufacturer_part_number='GRM1555C1H100JA01D')
    mp2.save()
    pt2.primary_manufacturer_part = mp2
    pt2.save()
    assy2 = create_a_fake_assembly_with_subpart(part_revision=pr1)
    pr2 = create_a_fake_part_revision(part=pt2, assembly=assy2)

    mp3 = ManufacturerPart(part=pt3, manufacturer=m3, manufacturer_part_number='NRF51822')
    mp3.save()
    assy3 = create_a_fake_assembly_with_subpart(part_revision=pr2)
    subpart3 = create_a_fake_subpart(pr1, count=10, reference="")
    subpart32 = create_a_fake_subpart(pr2, count=3, reference="")
    assy3.subparts.add(subpart3)
    assy3.subparts.add(subpart32)
    assy3.save()
    create_a_fake_part_revision(part=pt3, assembly=assy3)
    create_a_fake_part_revision(part=pt3, assembly=assy3, revision="2")

    # Create a part with a PartRevision with no assembly - no longer happens due to PartRevision save override
    create_a_fake_part_revision(pt5, None)

    (s1, s2, s3) = create_some_fake_sellers(organization=organization)

    create_a_fake_seller_part(s1, mp1, moq=1, mpq=1, unit_cost=0, lead_time_days=None, nre_cost=0,)
    create_a_fake_seller_part(s1, mp1, moq=1, mpq=1, unit_cost=1.2, lead_time_days=20, nre_cost=500)
    create_a_fake_seller_part(s2, mp1, moq=1000, mpq=5000, unit_cost=0.1005, lead_time_days=7, nre_cost=0)
    create_a_fake_seller_part(s2, mp2, moq=200, mpq=200, unit_cost=0.5, lead_time_days=47, nre_cost=1)
    create_a_fake_seller_part(s2, mp2, moq=2000, mpq=200, unit_cost=0.4, lead_time_days=47, nre_cost=10)
    create_a_fake_seller_part(s1, mp2, moq=2000, mpq=200, unit_cost=0.4, lead_time_days=47, nre_cost=10)
    create_a_fake_seller_part(s1, mp2, moq=3000, mpq=200, unit_cost=0.3, lead_time_days=47, nre_cost=10)
    return pt1, pt2, pt3, pt4


def create_some_fake_data(user):
    o = create_a_fake_organization(user)
    return create_some_fake_parts(o)


def create_all_part_classes():
    PartClass.objects.get_or_create(code=100, name='Assembly, Top Level SKU\'s, Finished Goods',
                                    comment='Ready to ship product; Includes packout and literature')
    PartClass.objects.get_or_create(code=101, name='Assembly, Final, Elec/Mech',
                                    comment='Fully built product without Literature or packout materials')
    PartClass.objects.get_or_create(code=102, name='Assembly, Sub-Assy, Elec/Mech', comment='')
    PartClass.objects.get_or_create(code=103, name='Assembly, Cable', comment='')
    PartClass.objects.get_or_create(code=104, name='Assembly, Packaging', comment='')
    PartClass.objects.get_or_create(code=105, name='Kit, Spare/Upgrade',
                                    comment='Includes Spares, Documentation Kits, Upgrade Kits')
    PartClass.objects.get_or_create(code=106, name='Assembly, Printed Circuit Board (PBCA)', comment='')
    PartClass.objects.get_or_create(code=107, name='PCB, Fab', comment='')
    PartClass.objects.get_or_create(code=108, name='Gerber File/X-Y data/CAD data', comment='')
    PartClass.objects.get_or_create(code=109, name='Schematic/Test Diagram', comment='')
    PartClass.objects.get_or_create(code=110, name='Mechanical Reference, PCB', comment='')
    PartClass.objects.get_or_create(code=111, name='Mechanical Reference, PCBA', comment='')
    PartClass.objects.get_or_create(code=112, name='Programmed Device, Firmware/Software', comment='')
    PartClass.objects.get_or_create(code=113, name='Program File, Firmware, Software', comment='')
    PartClass.objects.get_or_create(code=114, name='Build-To-Print, Sheet Metal', comment='')
    PartClass.objects.get_or_create(code=115, name='Build-To-Print, Cast Metal', comment='')
    PartClass.objects.get_or_create(code=116, name='Build-To-Print, Extruded',
                                    comment='Applies for both plastic and metal')
    PartClass.objects.get_or_create(code=117, name='Build-To-Print, Machined',
                                    comment='Applies for both plastic and metal')
    PartClass.objects.get_or_create(code=118, name='Build-To-Print, Molded',
                                    comment='Applies for both Injection and Compression molded')
    PartClass.objects.get_or_create(code=119, name='Build-To-Print, Formed',
                                    comment='Applies for plastic only; Formed sheet metal goes under sheet metal')
    PartClass.objects.get_or_create(code=120, name='Build-To-Print, Die-Cut',
                                    comment='Applied to foams and PSAs (Pressure Sensitive Adhesives)')
    PartClass.objects.get_or_create(code=121, name='Build-To-Print, Mechanical',
                                    comment='Applies to all Build-To-Print Mech Parts that do not fall into one of the other specific groups.')
    PartClass.objects.get_or_create(code=122, name='Build-To-Print, Printed Material',
                                    comment='Includes Custom Labels, Product Literature, etc.')
    PartClass.objects.get_or_create(code=123, name='Build-To-Print, Packaging', comment='')
    PartClass.objects.get_or_create(code=124, name='Drawing',
                                    comment='For any drawing that does not use the Item PN as the dwg PN')
    PartClass.objects.get_or_create(code=125, name='Artwork',
                                    comment='A/W for printing, silkscreening of labels, sheetmetal, etc.')
    PartClass.objects.get_or_create(code=126, name='Release Notes/Protocols', comment='')
    PartClass.objects.get_or_create(code=127, name='Custom Electronic Components', comment='Example: Custom Sensors')
    PartClass.objects.get_or_create(code=200, name='Adhesive', comment='Includes Loctite, Epoxy, Tape, etc.')
    PartClass.objects.get_or_create(code=201, name='Battery/Charger', comment='')
    PartClass.objects.get_or_create(code=202, name='Cable & Wire', comment='')
    PartClass.objects.get_or_create(code=203, name='Connector, Cable/Harness', comment='')
    PartClass.objects.get_or_create(code=204, name='Connector, PC Mountable', comment='')
    PartClass.objects.get_or_create(code=205, name='Connector, Misc', comment='')
    PartClass.objects.get_or_create(code=206, name='Cable Hardware', comment='Includes Clamps, Ties, etc.')
    PartClass.objects.get_or_create(code=207, name='Capacitor', comment='')
    PartClass.objects.get_or_create(code=208, name='Circuit Breaker/Filter', comment='')
    PartClass.objects.get_or_create(code=209, name='Crystal', comment='')
    PartClass.objects.get_or_create(code=210, name='Delay Line', comment='')
    PartClass.objects.get_or_create(code=211, name='Diode', comment='')
    PartClass.objects.get_or_create(code=212, name='Fan & Fan Accessories', comment='')
    PartClass.objects.get_or_create(code=213, name='Fuse & Fuse Hardware', comment='')
    PartClass.objects.get_or_create(code=214, name='Hardware',
                                    comment='Includes Screws, Nuts, Washers, Springs, Std-offs, Inserts, Fasteners, etc')
    PartClass.objects.get_or_create(code=215, name='Heatsink', comment='')
    PartClass.objects.get_or_create(code=216, name='IC', comment='')
    PartClass.objects.get_or_create(code=217, name='Inductor', comment='')
    PartClass.objects.get_or_create(code=218, name='Insulator', comment='')
    PartClass.objects.get_or_create(code=219, name='Label', comment='')
    PartClass.objects.get_or_create(code=220, name='Led', comment='/Light')
    PartClass.objects.get_or_create(code=221, name='Packaging Material',
                                    comment='Includes Bags, Boxes, Pallets, etc. Non-Custom only.')
    PartClass.objects.get_or_create(code=222, name='Rectifier', comment='')
    PartClass.objects.get_or_create(code=223, name='Resistor', comment='')
    PartClass.objects.get_or_create(code=224, name='Resistor Network', comment='')
    PartClass.objects.get_or_create(code=225, name='Socket', comment='')
    PartClass.objects.get_or_create(code=226, name='Switch', comment='')
    PartClass.objects.get_or_create(code=227, name='Terminal', comment='Includes Ring, Spade, Butt, etc.')
    PartClass.objects.get_or_create(code=228, name='Transformer', comment='')
    PartClass.objects.get_or_create(code=229, name='Transistor', comment='')
    PartClass.objects.get_or_create(code=230, name='Tubing', comment='All types')
    PartClass.objects.get_or_create(code=231, name='Sensor', comment='')
    PartClass.objects.get_or_create(code=232, name='Power Supply', comment='')
    PartClass.objects.get_or_create(code=233, name='Enclosures', comment='Off-The-Shelf Enclosures')
    PartClass.objects.get_or_create(code=234, name='Varistor', comment='')
    PartClass.objects.get_or_create(code=235, name='Ferrites', comment='')
    PartClass.objects.get_or_create(code=236, name='Suppressor', comment='')
    PartClass.objects.get_or_create(code=237, name='Misc Material', comment='')
    PartClass.objects.get_or_create(code=238, name='Electronic Assy (Non-Custom)', comment='')
    PartClass.objects.get_or_create(code=239, name='Antenna', comment='')
