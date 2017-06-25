from bom.octopart_parts_match import match_part
from bom.models import Part, PartClass, Seller, SellerPart, Subpart, \
    Manufacturer, Organization, PartFile


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
        part,
        moq,
        mpq,
        unit_cost,
        lead_time_days):
    sp1 = SellerPart(
        seller=seller,
        part=part,
        minimum_order_quantity=moq,
        minimum_pack_quantity=mpq,
        unit_cost=unit_cost,
        lead_time_days=lead_time_days)
    sp1.save()

    return sp1


def create_some_fake_parts(organization):
    (pc1, pc2, pc3) = create_some_fake_part_classes()
    (m1, m2, m3) = create_some_fake_manufacturers(organization=organization)

    pt1 = Part(
        manufacturer_part_number='STM32F401CEU6',
        number_class=pc2,
        number_item='3333',
        description='Brown dog',
        revision='1',
        manufacturer=m1,
        organization=organization)
    pt1.save()

    pt2 = Part(
        manufacturer_part_number='GRM1555C1H100JA01D',
        number_class=pc1,
        description='',
        manufacturer=None,
        organization=organization)
    pt2.save()

    pt3 = Part(
        manufacturer_part_number='NRF51822',
        number_class=pc3,
        description='Friendly ghost',
        manufacturer=m3,
        organization=organization)
    pt3.save()

    create_a_fake_subpart(pt1, pt2)
    create_a_fake_subpart(pt1, pt3, count=10)

    (s1, s2, s3) = create_some_fake_sellers(organization=organization)

    create_a_fake_seller_part(
        s1,
        pt1,
        moq=None,
        mpq=None,
        unit_cost=None,
        lead_time_days=None)
    create_a_fake_seller_part(
        s2,
        pt1,
        moq=1000,
        mpq=5000,
        unit_cost=0.1005,
        lead_time_days=7)
    create_a_fake_seller_part(
        s2,
        pt2,
        moq=200,
        mpq=200,
        unit_cost=0,
        lead_time_days=47)

    return pt1, pt2, pt3


def create_a_fake_partfile(file, part):
    pf1 = PartFile(file=None, part=part)
    pf1.save()

    return pf1
