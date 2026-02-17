from django.urls import path
from django.contrib.auth import views as auth_views
from . import views
from django.views.generic.base import RedirectView

urlpatterns = [
    # Authentication
    path('register/', views.register, name='register'),
    path('login/', views.custom_login, name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),

    # Curriculum setup flow
    path('concentration/', views.concentration_selection, name='concentration_selection'),
    path('save-concentration/', views.save_concentration, name='save_concentration'),
    path('courses/', views.course_checklist, name='course_checklist'),
    path('save-course-progress/', views.save_course_progress, name='save_course_progress'),
    path('migrate-user-data/', views.migrate_user_data_add_userid, name='migrate_user_data'),
    # API endpoints
    path('api/curriculum-courses/', views.api_curriculum_courses, name='api_curriculum_courses'),
    path('api/available-concentrations/', views.api_available_concentrations, name='api_available_concentrations'),

    # PolyTech Companion Chatbot (bubble assistant)
    path('ai-chatbot-page/', views.ai_chatbot_page, name='ai_chatbot_page'),

    # Main dashboard
    path('dashboard/', views.dashboard, name='dashboard'),

    path('accounts/', RedirectView.as_view(pattern_name='dashboard', permanent=False)),

    path('plan-next-trimester/', views.plan_trimester, name='plan_trimester'),

    path('api/available-courses/', views.api_available_courses, name='api_available_courses'),

    path('save-trimester-plan/', views.save_trimester_plan, name='save_trimester_plan'),

    path('api/user-plans/', views.get_user_plans, name='get_user_plans'),

    path('delete-plan/<str:plan_id>/', views.delete_plan, name='delete_plan'),
    path('course-checklist/', views.course_checklist, name='update_progress'),
    path('update-plan/<str:plan_id>/', views.update_plan, name='update_plan'),
    path('edit-trimester-plans/', views.edit_plans, name='edit_plans'),
    path('api/completed-courses/', views.api_completed_courses, name='api_completed_courses'),

    # AI Suggestions
    path('api/structured-ai-suggestion/', views.structured_ai_suggestion, name='structured_ai_suggestion'),

    # User preferences
    path('api/save-card-style/', views.save_card_style, name='save_card_style'),

    # Password reset
    path('api/password-reset/', views.password_reset_request, name='password_reset_request'),
    path('reset-password/<uidb64>/<token>/', views.password_reset_confirm, name='password_reset_confirm'),



]