from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^$', views.home, name='home'),
    url(r'^error/$', views.error, name='error'),
    url(r'^signup/$', views.bom_signup, name='bom-signup'),
    url(r'^export/$', views.export_part_list, name='export-part-list'),
    url(r'^create-part/$', views.create_part, name='create-part'),
    url(r'^upload-parts/$', views.upload_parts, name='upload-parts'),
    url(r'^part/(?P<part_id>[0-9]+)/$', views.part_info, name='part-info'),
    url(r'^part/(?P<part_id>[0-9]+)/export/$', views.part_export_bom, name='part-export-bom'),
    url(r'^part/(?P<part_id>[0-9]+)/upload/$', views.part_upload_bom, name='part-upload-bom'),
    url(r'^part/(?P<part_id>[0-9]+)/octopart-match/$', views.part_octopart_match, name='part-octopart-match'),
    url(r'^part/(?P<part_id>[0-9]+)/octopart-match-indented/$', views.part_octopart_match_bom, name='part-octopart-match-bom'),
    url(r'^part/(?P<part_id>[0-9]+)/edit/$', views.part_edit, name='part-edit'),
    url(r'^part/(?P<part_id>[0-9]+)/delete/$', views.part_delete, name='part-delete'),
    url(r'^part/(?P<part_id>[0-9]+)/manage-bom/$', views.manage_bom, name='part-manage-bom'),
    url(r'^part/(?P<part_id>[0-9]+)/add-subpart/$', views.add_subpart, name='part-add-subpart'),
    url(r'^part/(?P<part_id>[0-9]+)/upload-file/$', views.upload_file_to_part, name='part-upload-partfile'),
    url(r'^part/(?P<part_id>[0-9]+)/delete-file/(?P<partfile_id>[0-9]+)/$', views.delete_file_from_part, name='part-delete-partfile'),
    url(r'^part/(?P<part_id>[0-9]+)/remove-all-subparts/$', views.remove_all_subparts, name='part-remove-all-subparts'),
    url(r'^part/(?P<part_id>[0-9]+)/remove-subpart/(?P<subpart_id>[0-9]+)/$', views.remove_subpart, name='part-remove-subpart'),
]
