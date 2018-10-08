from bom.octopart import match_part
from bom.models import Part, PartClass, Seller, SellerPart, Subpart, \
    Manufacturer, Organization, PartFile, ManufacturerPart


def create_a_fake_organization(user, free=False):
    org = Organization(
        name="Atlas",
        subscription='F' if free else 'P',
        owner=user)
    org.save()

    return org


def create_some_fake_part_classes():
    pc1 = PartClass(code=500, name='Wendy', comment='Mechanical Switches')
    pc1.save()

    pc2 = PartClass(code=200, name='Archibald', comment='')
    pc2.save()

    pc3 = PartClass(code=503, name='Ghost', comment='Like Kasper')
    pc3.save()

    return pc1, pc2, pc3


def create_a_fake_subpart(assembly_part, assembly_subpart, count=4):
    sp1 = Subpart(
        assembly_part=assembly_part,
        assembly_subpart=assembly_subpart,
        count=count)
    sp1.save()

    return sp1


def create_some_fake_sellers(organization):
    s1 = Seller(name='Mouser', organization=organization)
    s1.save()

    s2 = Seller(name='Digi-Key', organization=organization)
    s2.save()

    s3 = Seller(name='Archibald', organization=organization)
    s3.save()

    return s1, s2, s3


def create_some_fake_manufacturers(organization):
    m1 = Manufacturer(name='STMicroelectronics', organization=organization)
    m1.save()

    m2 = Manufacturer(name='Nordic Semiconductor', organization=organization)
    m2.save()

    m3 = Manufacturer(name='Murata', organization=organization)
    m3.save()

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


def create_some_fake_parts(organization):
    (pc1, pc2, pc3) = create_some_fake_part_classes()
    (m1, m2, m3) = create_some_fake_manufacturers(organization=organization)

    pt1 = Part(
        number_class=pc2,
        number_item='3333',
        description='Brown dog',
        revision='1',
        organization=organization)
    pt1.save()
    mp1 = ManufacturerPart(part=pt1, manufacturer=m1, manufacturer_part_number='STM32F401CEU6')
    mp1.save()
    pt1.primary_manufacturer_part = mp1
    pt1.save()

    pt2 = Part(
        number_class=pc1,
        description='',
        organization=organization)
    pt2.save()
    mp2 = ManufacturerPart(part=pt2, manufacturer=None, manufacturer_part_number='GRM1555C1H100JA01D')
    mp2.save()
    pt2.primary_manufacturer_part = mp2
    pt2.save()

    pt3 = Part(
        number_class=pc3,
        description='Friendly ghost',
        organization=organization)
    pt3.save()
    mp3 = ManufacturerPart(part=pt3, manufacturer=m3, manufacturer_part_number='NRF51822')
    mp3.save()

    create_a_fake_subpart(pt1, pt2)
    create_a_fake_subpart(pt1, pt3, count=10)

    (s1, s2, s3) = create_some_fake_sellers(organization=organization)

    create_a_fake_seller_part(
        s1,
        mp1,
        moq=None,
        mpq=None,
        unit_cost=None,
        lead_time_days=None)
    create_a_fake_seller_part(
        s1,
        mp1,
        moq=1,
        mpq=1,
        unit_cost=1.2,
        lead_time_days=20,
        nre_cost=500)
    create_a_fake_seller_part(
        s2,
        mp1,
        moq=1000,
        mpq=5000,
        unit_cost=0.1005,
        lead_time_days=7,)
    create_a_fake_seller_part(
        s2,
        mp2,
        moq=200,
        mpq=200,
        unit_cost=0.5,
        lead_time_days=47,
        nre_cost=1)

    return pt1, pt2, pt3


def create_a_fake_partfile(file, part):
    pf1 = PartFile(file=None, part=part)
    pf1.save()

    return pf1
