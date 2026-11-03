from django.urls import include, path

from bom.urls import urlpatterns

urlpatterns = urlpatterns + [path('o/', include('oauth2_provider.urls', namespace='oauth2_provider'))]
