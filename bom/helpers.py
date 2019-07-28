from bom.models import Part, PartClass, Seller, SellerPart, Subpart, \
    Manufacturer, Organization, ManufacturerPart, PartRevision, Assembly


def create_a_fake_organization(user, free=False):
    org, created = Organization.objects.get_or_create(
        name="Atlas",
        subscription='F' if free else 'P',
        owner=user)
    return org


def create_some_fake_part_classes():
    pc1, c = PartClass.objects.get_or_create(code=500, name='Wendy', comment='Mechanical Switches')
    pc2, c = PartClass.objects.get_or_create(code=200, name='Archibald', comment='')
    pc3, c = PartClass.objects.get_or_create(code=503, name='Ghost', comment='Like Kasper')
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


def create_some_fake_parts(organization):
    (pc1, pc2, pc3) = create_some_fake_part_classes()
    (m1, m2, m3) = create_some_fake_manufacturers(organization=organization)

    pt1 = Part(
        number_class=pc2,
        number_item='3333',
        organization=organization)
    pt1.save()
    mp1 = ManufacturerPart(part=pt1, manufacturer=m1, manufacturer_part_number='STM32F401CEU6')
    mp1.save()
    pt1.primary_manufacturer_part = mp1
    pt1.save()
    assy = create_a_fake_assembly()
    pch1 = create_a_fake_part_revision(part=pt1, assembly=assy)

    pt2 = Part(
        number_class=pc1,
        organization=organization)
    pt2.save()
    mp2 = ManufacturerPart(part=pt2, manufacturer=None, manufacturer_part_number='GRM1555C1H100JA01D')
    mp2.save()
    pt2.primary_manufacturer_part = mp2
    pt2.save()
    assy2 = create_a_fake_assembly_with_subpart(part_revision=pch1)
    pch2 = create_a_fake_part_revision(part=pt2, assembly=assy2)

    pt3 = Part(number_class=pc3, organization=organization)
    pt3.save()
    mp3 = ManufacturerPart(part=pt3, manufacturer=m3, manufacturer_part_number='NRF51822')
    mp3.save()
    assy3 = create_a_fake_assembly_with_subpart(part_revision=pch2)
    subpart3 = create_a_fake_subpart(pch1, count=10, reference="")
    assy3.subparts.add(subpart3)
    create_a_fake_part_revision(part=pt3, assembly=assy3)
    create_a_fake_part_revision(part=pt3, assembly=assy3)

    # Create a part with no PartChangeHistory
    pt4 = Part(number_class=pc1, number_item='4444', organization=organization)
    pt4.save()

    # Create a part with a PartChangeHistory with no assembly
    pt5 = Part(number_class=pc1, number_item='5555', organization=organization)
    pt5.save()
    create_a_fake_part_revision(pt5, None)

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
        lead_time_days=7, )
    create_a_fake_seller_part(
        s2,
        mp2,
        moq=200,
        mpq=200,
        unit_cost=0.5,
        lead_time_days=47,
        nre_cost=1)

    return pt1, pt2, pt3, pt4


def create_some_fake_data(user):
    o = create_a_fake_organization(user)
    return create_some_fake_parts(o)
