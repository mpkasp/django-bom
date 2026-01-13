from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from .models import (
    Assembly,
    Manufacturer,
    ManufacturerPart,
    Part,
    PartClass,
    PartRevision,
    PartRevisionProperty,
    PartRevisionPropertyDefinition,
    Seller,
    SellerPart,
    Subpart,
    UnitDefinition,
    QuantityOfMeasure,
    get_organization_model,
    get_user_meta_model
)

User = get_user_model()
UserMeta = get_user_meta_model()
Organization = get_organization_model()

class UserMetaInline(admin.TabularInline):
    model = UserMeta
    verbose_name = 'BOM User Meta'
    raw_id_fields = ('organization',)
    can_delete = False


class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name',)


class SubpartInline(admin.TabularInline):
    model = Subpart
    fk_name = 'part_revision'
    raw_id_fields = ('part_revision',)
    # readonly_fields = ('get_full_part_number', )
    #
    # def get_full_part_number(self, obj):
    #     return obj.assembly_subpart.part.full_part_number()
    # get_full_part_number.short_description = 'PartNumber'


class SellerAdmin(admin.ModelAdmin):
    list_display = ('name',)


class SellerPartAdmin(admin.ModelAdmin):
    list_display = (
        'manufacturer_part',
        'seller',
        'seller_part_number',
        'minimum_order_quantity',
        'minimum_pack_quantity',
        'unit_cost',
        'lead_time_days',
        'nre_cost',
        'ncnr')


class SellerPartAdminInline(admin.TabularInline):
    model = SellerPart
    raw_id_fields = ('seller', 'manufacturer_part',)


class ManufacturerPartAdmin(admin.ModelAdmin):
    list_display = (
        'manufacturer_part_number',
        'manufacturer',
        'part',)
    raw_id_fields = ('manufacturer', 'part',)
    inlines = [
        SellerPartAdminInline,
    ]


class ManufacturerPartAdminInline(admin.TabularInline):
    model = ManufacturerPart
    raw_id_fields = ('part', 'manufacturer',)


class PartClassAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'comment',)


class PartRevisionAdminInline(admin.TabularInline):
    model = PartRevision
    extra = 0
    raw_id_fields = ('assembly',)
    readonly_fields = ('timestamp',)
    show_change_link = True


class PartAdmin(admin.ModelAdmin):
    ordering = ('organization', 'number_class__code', 'number_item', 'number_variation')
    readonly_fields = ('get_full_part_number',)
    list_display = (
        'organization',
        'get_full_part_number',
    )
    list_filter = ('organization', 'number_class',)
    raw_id_fields = ('number_class', 'primary_manufacturer_part',)
    inlines = [
        PartRevisionAdminInline,
        ManufacturerPartAdminInline,
    ]

    def get_full_part_number(self, obj):
        return obj.full_part_number()

    get_full_part_number.short_description = 'PartNumber'
    get_full_part_number.admin_order_field = 'number_class__part_number'


class QuantityOfMeasureAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization')
    list_filter = ('organization',)


class PartRevisionPropertyInline(admin.TabularInline):
    model = PartRevisionProperty
    extra = 1
    raw_id_fields = ('property_definition', 'part_revision', 'unit_definition')


class PartRevisionAdmin(admin.ModelAdmin):
    list_display = ('part', 'revision', 'description', 'get_assembly_size', 'timestamp',)
    raw_id_fields = ('assembly',)
    readonly_fields = ('timestamp',)
    inlines = [PartRevisionPropertyInline]

    def get_assembly_size(self, obj):
        return None if obj.assembly is None else obj.assembly.subparts.count()

    get_assembly_size.short_description = 'AssemblySize'


class ManufacturerAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization',)


class SubpartAdmin(admin.ModelAdmin):
    list_display = ('part_revision', 'count', 'reference',)


class SubpartsInline(admin.TabularInline):
    model = Assembly.subparts.through
    raw_id_fields = ('subpart',)


class AssemblyAdmin(admin.ModelAdmin):
    list_display = ('id',)
    exclude = ('subparts',)
    inlines = [
        SubpartsInline,
    ]


class UnitDefinitionAdmin(admin.ModelAdmin):
    list_display = ('name', 'symbol', 'quantity_of_measure', 'base_multiplier', 'organization')
    list_filter = ('organization',)


class PartRevisionPropertyDefinitionAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'type', 'organization', 'quantity_of_measure')
    list_filter = ('organization',)


if settings.BOM_USER_META_MODEL == 'bom.UserMeta':
    current_user_admin = admin.site._registry.get(User)

    if current_user_admin:
        user_admin_class = current_user_admin.__class__
        existing_inlines = list(user_admin_class.inlines or [])
        if UserMetaInline not in existing_inlines:
            existing_inlines.append(UserMetaInline)
            user_admin_class.inlines = existing_inlines
    else:
        class BomUserAdmin(UserAdmin):
            inlines = [UserMetaInline]


        admin.site.register(User, BomUserAdmin)

if settings.BOM_ORGANIZATION_MODEL == 'bom.Organization':
    from .models import Organization

    admin.site.register(Organization, OrganizationAdmin)

admin.site.register(Seller, SellerAdmin)
admin.site.register(SellerPart, SellerPartAdmin)
admin.site.register(ManufacturerPart, ManufacturerPartAdmin)
admin.site.register(PartClass, PartClassAdmin)
admin.site.register(Part, PartAdmin)
admin.site.register(PartRevision, PartRevisionAdmin)
admin.site.register(Manufacturer, ManufacturerAdmin)
admin.site.register(Assembly, AssemblyAdmin)
admin.site.register(Subpart, SubpartAdmin)
admin.site.register(UnitDefinition, UnitDefinitionAdmin)
admin.site.register(PartRevisionPropertyDefinition, PartRevisionPropertyDefinitionAdmin)
admin.site.register(QuantityOfMeasure, QuantityOfMeasureAdmin)
