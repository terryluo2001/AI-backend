"""
URL configuration for AI_backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from AI_backend.user import (register, update_user, login,)
from AI_backend.article import (add_article, get_article, toggle_like, toggle_dislike, answer)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('register/', register),
    path('update-user/', update_user),
    path('login/', login),
    path('add-article/', add_article),
    path('get-articles/', get_article),
    path('toggle-like/<int:article_id>/', toggle_like),
    path('toggle-dislike/<int:article_id>/', toggle_dislike),
    path('answer/', answer)
]
