from django.conf.urls import include
from django.urls import path
from django.contrib.auth import views as auth_views
from django.contrib import admin

from . import views
from . import google_drive

bom_patterns = [
    # BOM urls
    path('', views.home, name='home'),
    path('error/', views.error, name='error'),
    path('signup/', views.bom_signup, name='bom-signup'),
    path('settings/', views.bom_settings, name='settings'),
    path('settings/<str:tab_anchor>/', views.bom_settings, name='settings'),
    path('export/', views.export_part_list, name='export-part-list'),
    path('create-part/', views.create_part, name='create-part'),
    path('upload-parts/', views.upload_parts, name='upload-parts'),
    path('part/<int:part_id>/', views.part_info, name='part-info'),
    path('part/<int:part_id>/export/', views.part_export_bom, name='part-export-bom'),
    path('part/<int:part_id>/upload/', views.part_upload_bom, name='part-upload-bom'),
    path('part/<int:part_id>/octopart-match/', views.part_octopart_match, name='part-octopart-match'),
    path('part/<int:part_id>/octopart-match-indented/', views.part_octopart_match_bom, name='part-octopart-match-bom'),
    path('part/<int:part_id>/edit/', views.part_edit, name='part-edit'),
    path('part/<int:part_id>/delete/', views.part_delete, name='part-delete'),
    path('part/<int:part_id>/manage-bom/', views.manage_bom, name='part-manage-bom'),
    path('part/<int:part_id>/add-subpart/', views.add_subpart, name='part-add-subpart'),
    path('part/<int:part_id>/edit-subpart/<int:subpart_id>', views.edit_subpart, name='part-edit-subpart'),
    path('part/<int:part_id>/add-manufacturer-part/', views.add_manufacturer_part, name='part-add-manufacturer-part'),
    path('part/<int:part_id>/upload-file/', views.upload_file_to_part, name='part-upload-partfile'),
    path('part/<int:part_id>/delete-file/<int:partfile_id>/', views.delete_file_from_part, name='part-delete-partfile'),
    path('part/<int:part_id>/remove-all-subparts/', views.remove_all_subparts, name='part-remove-all-subparts'),
    path('part/<int:part_id>/remove-subpart/<int:subpart_id>/', views.remove_subpart, name='part-remove-subpart'),
    path('sellerpart/<int:sellerpart_id>/edit/', views.sellerpart_edit, name='sellerpart-edit'),
    path('sellerpart/<int:sellerpart_id>/delete/', views.sellerpart_delete, name='sellerpart-delete'),
    path('manufacturer-part/<int:manufacturer_part_id>/add-sellerpart/', views.add_sellerpart, name='manufacturer-part-add-sellerpart'),
    path('manufacturer-part/<int:manufacturer_part_id>/edit', views.manufacturer_part_edit, name='manufacturer-part-edit'),
    path('manufacturer-part/<int:manufacturer_part_id>/delete', views.manufacturer_part_delete, name='manufacturer-part-delete'),
    path('manufacturer-part/<int:manufacturer_part_id>/octopart-match/', views.manufacturer_part_octopart_match, name='manufacturer-part-octopart-match'),
]

google_drive_patterns = [
    path('add-folder/<int:part_id>/', google_drive.get_or_create_and_open_folder, name='add-folder'),
]

urlpatterns = [
    path('', include((bom_patterns, 'bom'))),
    path('', include('social_django.urls', namespace='social')),
    path('', include((google_drive_patterns, 'google-drive'))),

    # you will likely have your own implementation of these in your app
    path('admin/', admin.site.urls),
    path('login/', auth_views.LoginView.as_view(), {'redirect_authenticated_user': True, }, name='login'),
    path('logout/', auth_views.LogoutView.as_view(), {'next_page': '/'}, name='logout'),
]