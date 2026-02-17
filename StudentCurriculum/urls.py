# StudentCurriculum/urls.py - Updated main project URLs
from django.contrib import admin
from django.urls import path, include
from django.contrib.auth.views import LogoutView
from django.http import HttpResponse
from accounts.views import home_view  
#from accounts.views import concentration_selection

# def home(request):
#     """Basic home page - no redirects"""
#     return HttpResponse("<h1>Curriculum Planner Home</h1><p>Navigate to: <a href='/accounts/register/'>Register</a> | <a href='/accounts/login/'>Login</a> | <a href='/accounts/concentration/'>Concentration</a> | <a href='/accounts/course-checklist/'>Course Checklist</a> | <a href='/accounts/dashboard/'>Dashboard</a></p>")

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('', home_view, name='home'),  # Original home view
    #path('', concentration_selection, name='home'),  # Changed to concentration selection
   
]