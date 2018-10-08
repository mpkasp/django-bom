from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import *


class UserMetaInline(admin.TabularInline):
    model = UserMeta
    raw_id_fields = ('organization', )
    can_delete = False


class UserAdmin(UserAdmin):
    inlines = (UserMetaInline, )


class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', )


class SubpartInline(admin.TabularInline):
    model = Subpart
    fk_name = 'assembly_part'
    raw_id_fields = ('assembly_subpart', )
    readonly_fields = ('get_full_part_number', )

    def get_full_part_number(self, obj):
        return obj.assembly_subpart.full_part_number()
    get_full_part_number.short_description = 'PartNumber'


class SellerAdmin(admin.ModelAdmin):
    list_display = ('name', )


class SellerPartAdmin(admin.ModelAdmin):
    list_display = (
        'manufacturer_part',
        'seller',
        'minimum_order_quantity',
        'minimum_pack_quantity',
        'unit_cost',
        'lead_time_days',
        'nre_cost',
        'ncnr')


class SellerPartAdminInline(admin.TabularInline):
    model = SellerPart
    raw_id_fields = ('seller', 'manufacturer_part', )


class ManufacturerPartAdmin(admin.ModelAdmin):
    list_display = (
        'manufacturer_part_number',
        'manufacturer',
        'part',)
    raw_id_fields = ('manufacturer', 'part', )
    inlines = [
        SellerPartAdminInline,
    ]


class ManufacturerPartAdminInline(admin.TabularInline):
    model = ManufacturerPart
    raw_id_fields = ('part', 'manufacturer', )


class PartFileAdmin(admin.ModelAdmin):
    list_display = ('file', 'upload_date', 'get_full_part_number')
    raw_id_fields = ('part',)

    def get_full_part_number(self, obj):
        return obj.part.full_part_number()
    get_full_part_number.short_description = 'PartNumber'


class PartFileAdminInline(admin.TabularInline):
    model = PartFile
    raw_id_fields = ('part', )


class PartClassAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'comment', )


class PartAdmin(admin.ModelAdmin):
    ordering = ('organization', 'number_class__code', 'number_item', 'number_variation')
    readonly_fields = ('get_full_part_number', )
    list_display = (
        'organization',
        'get_full_part_number',
        'revision',
        'description',
    )
    raw_id_fields = ('number_class', 'primary_manufacturer_part', )
    inlines = [
        SubpartInline,
        ManufacturerPartAdminInline,
        PartFileAdminInline,
    ]

    def get_full_part_number(self, obj):
        return obj.full_part_number()
    get_full_part_number.short_description = 'PartNumber'
    get_full_part_number.admin_order_field = 'number_class__part_number'


class ManufacturerAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization', )


admin.site.unregister(User)

admin.site.register(User, UserAdmin)
admin.site.register(Organization, OrganizationAdmin)
admin.site.register(Seller, SellerAdmin)
admin.site.register(SellerPart, SellerPartAdmin)
admin.site.register(ManufacturerPart, ManufacturerPartAdmin)
admin.site.register(PartClass, PartClassAdmin)
admin.site.register(Part, PartAdmin)
admin.site.register(Manufacturer, ManufacturerAdmin)
admin.site.register(PartFile, PartFileAdmin)
