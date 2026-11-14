from django.urls import path

from catalog import views

urlpatterns = [
    path("api/products/", views.products),
    path("api/products/<int:pk>/", views.products),
    path("api/clear/", views.clear),
    path("api/imports/", views.upload),
    path("api/jobs/<str:job_id>/", views.job),
    path("api/webhooks/", views.hooks),
    path("api/webhooks/<int:pk>/", views.hooks),
    path("api/webhooks/<int:pk>/test/", views.test_hook),
]

