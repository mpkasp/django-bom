from django.conf.urls import include
from django.urls import path
from django.contrib.auth import views as auth_views
from django.contrib import admin

from bom.views import views, json_views
from bom.third_party_apis import google_drive

bom_patterns = [
    # BOM urls
    path('', views.home, name='home'),
    # path('bom/', views.home, name='home'),
    path('create-organization/', views.organization_create, name='organization-create'),
    path('help/', views.Help.as_view(), name=views.Help.name),
    path('search-help/', views.search_help, name='search-help'),
    path('bom-signup/', views.bom_signup, name='bom-signup'),
    path('settings/', views.bom_settings, name='settings'),
    path('settings/<str:tab_anchor>/', views.bom_settings, name='settings'),
    path('export/', views.export_part_list, name='export-part-list'),
    path('user-meta/<int:user_meta_id>/edit/', views.user_meta_edit, name='user-meta-edit'),
    path('part-class/<int:part_class_id>/edit/', views.part_class_edit, name='part-class-edit'),
    path('create-part/', views.create_part, name='create-part'),
    path('upload-parts/', views.upload_parts, name='upload-parts'),
    path('upload-parts-help/', views.upload_parts_help, name='upload-parts-help'),
    path('upload-bom/', views.upload_bom, name='upload-bom'),
    path('part/<int:part_id>/', views.part_info, name='part-info'),
    path('part/<int:part_id>/export/', views.part_export_bom, name='part-export-bom'),
    path('part/<int:part_id>/export-sourcing/', views.part_export_bom, name='part-export-bom-sourcing', kwargs={'sourcing': True}),
    path('part/<int:part_id>/export-sourcing-detailed/', views.part_export_bom, name='part-export-bom-sourcing-detailed', kwargs={'sourcing_detailed': True}),
    path('part/<int:part_id>/upload/', views.part_upload_bom, name='part-upload-bom'),
    path('part/<int:part_id>/edit/', views.part_edit, name='part-edit'),
    path('part/<int:part_id>/delete/', views.part_delete, name='part-delete'),
    path('part/<int:part_id>/add-manufacturer-part/', views.add_manufacturer_part, name='part-add-manufacturer-part'),
    path('part/<int:part_id>/rev/new/', views.part_revision_new, name='part-revision-new'),
    path('part/<int:part_id>/rev/<int:part_revision_id>/', views.part_info, name='part-info-history'),
    path('part/<int:part_id>/rev/<int:part_revision_id>/edit/', views.part_revision_edit, name='part-revision-edit'),
    path('part/<int:part_id>/rev/<int:part_revision_id>/delete/', views.part_revision_delete, name='part-revision-delete'),
    path('part/<int:part_id>/rev/<int:part_revision_id>/release/', views.part_revision_release, name='part-revision-release'),
    path('part/<int:part_id>/rev/<int:part_revision_id>/revert/', views.part_revision_revert, name='part-revision-revert'),
    path('part/<int:part_id>/rev/<int:part_revision_id>/remove-all-subparts/', views.remove_all_subparts, name='part-remove-all-subparts'),
    path('part/<int:part_id>/rev/<int:part_revision_id>/edit-subpart/<int:subpart_id>', views.edit_subpart, name='part-edit-subpart'),
    path('part/<int:part_id>/rev/<int:part_revision_id>/remove-subpart/<int:subpart_id>/', views.remove_subpart, name='part-remove-subpart'),
    path('part/<int:part_id>/rev/<int:part_revision_id>/manage-bom/', views.manage_bom, name='part-manage-bom'),
    path('part/<int:part_id>/rev/<int:part_revision_id>/add-subpart/', views.add_subpart, name='part-add-subpart'),

    path('part-rev/<int:part_revision_id>/export/', views.part_export_bom, name='part-revision-export-bom'),
    path('part-rev/<int:part_revision_id>/export-sourcing/', views.part_export_bom, name='part-revision-export-bom-sourcing', kwargs={'sourcing': True}),
    path('part-rev/<int:part_revision_id>/export-sourcing-detailed/', views.part_export_bom, name='part-revision-export-bom-sourcing-detailed', kwargs={'sourcing_detailed': True}),
    path('part-rev/<int:part_revision_id>/export-flat/', views.part_export_bom, name='part-revision-export-bom-flat', kwargs={'flat': True}),
    path('part-rev/<int:part_revision_id>/export-flat-sourcing/', views.part_export_bom, name='part-revision-export-bom-flat-sourcing', kwargs={'flat': True, 'sourcing': True}),
    path('part-rev/<int:part_revision_id>/export-flat-sourcing-detailed/', views.part_export_bom, name='part-revision-export-bom-flat-sourcing-detailed', kwargs={'flat': True, 'sourcing_detailed': True}),

    path('sellerpart/<int:sellerpart_id>/edit/', views.sellerpart_edit, name='sellerpart-edit'),
    path('sellerpart/<int:sellerpart_id>/delete/', views.sellerpart_delete, name='sellerpart-delete'),
    path('manufacturer-part/<int:manufacturer_part_id>/add-sellerpart/', views.add_sellerpart, name='manufacturer-part-add-sellerpart'),
    path('manufacturer-part/<int:manufacturer_part_id>/edit', views.manufacturer_part_edit, name='manufacturer-part-edit'),
    path('manufacturer-part/<int:manufacturer_part_id>/delete', views.manufacturer_part_delete, name='manufacturer-part-delete'),
]

google_drive_patterns = [
    path('folder/<int:part_id>/', google_drive.get_or_create_and_open_folder, name='add-folder'),
]

json_patterns = [
    path('mouser-part-match-bom/<int:part_revision_id>/', json_views.MouserPartMatchBOM.as_view(), name='mouser-part-match-bom')
]

urlpatterns = [
    path('', include((bom_patterns, 'bom'))),
    path('', include('social_django.urls', namespace='social')),
    path('google-drive/', include((google_drive_patterns, 'google-drive'))),
    path('json/', include((json_patterns, 'json'))),

    # you will likely have your own implementation of these in your app
    path('admin/', admin.site.urls),
    path('signup/', views.signup, name='signup'),
    path('login/', auth_views.LoginView.as_view(), {'redirect_authenticated_user': True, }, name='login'),
    path('logout/', auth_views.LogoutView.as_view(), {'next_page': '/'}, name='logout'),
]
