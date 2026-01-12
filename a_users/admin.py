from django.contrib import admin

from .models import Profile, SupportEnquiry, UserReport


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
	list_display = ('user', 'displayname', 'chat_blocked', 'is_private_account')
	search_fields = ('user__username', 'displayname')


@admin.register(UserReport)
class UserReportAdmin(admin.ModelAdmin):
	list_display = ('id', 'status', 'reason', 'reporter', 'reported_user', 'created_at')
	list_filter = ('status', 'reason')
	search_fields = ('reporter__username', 'reported_user__username')


@admin.register(SupportEnquiry)
class SupportEnquiryAdmin(admin.ModelAdmin):
	list_display = ('id', 'status', 'user', 'subject', 'created_at')
	list_filter = ('status',)
	search_fields = ('user__username', 'subject', 'message')