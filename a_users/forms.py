from django import forms
from .models import Profile
from .models import UserReport
from .models import SupportEnquiry

class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['image', 'displayname', 'info']
        labels = {
            'info': 'Bio',
        }
        widgets = {
            'image': forms.FileInput(attrs={
                'class': 'w-full text-sm text-gray-300 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-gray-700 file:text-white hover:file:bg-gray-600',
            }),
            'displayname': forms.TextInput(attrs={
                'placeholder': 'Add display name',
                'class': 'w-full bg-gray-700 text-white border border-gray-600 rounded-lg px-4 py-3 outline-none focus:border-emerald-500',
            }),
            'info': forms.Textarea(attrs={
                'rows': 3,
                'placeholder': 'Add bio',
                'class': 'w-full bg-gray-700 text-white border border-gray-600 rounded-lg px-4 py-3 outline-none focus:border-emerald-500',
            })
        }


class ReportUserForm(forms.Form):
    reason = forms.ChoiceField(
        choices=UserReport.REASON_CHOICES,
        widget=forms.Select(
            attrs={
                'class': 'w-full bg-gray-700 text-white border border-gray-600 rounded-lg px-4 py-3 outline-none focus:border-emerald-500',
            }
        ),
    )


class ProfilePrivacyForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['is_private_account', 'is_stealth', 'is_dnd']
        widgets = {
            'is_private_account': forms.CheckboxInput(attrs={
                'class': 'sr-only peer',
            }),
            'is_stealth': forms.CheckboxInput(attrs={
                'class': 'sr-only peer',
            }),
            'is_dnd': forms.CheckboxInput(attrs={
                'class': 'sr-only peer',
            }),
        }
    details = forms.CharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(
            attrs={
                'rows': 3,
                'placeholder': 'Add details (optional)',
                'class': 'w-full bg-gray-700 text-white border border-gray-600 rounded-lg px-4 py-3 outline-none focus:border-emerald-500',
            }
        ),
    )


class SupportEnquiryForm(forms.ModelForm):
    class Meta:
        model = SupportEnquiry
        fields = ['subject', 'message']
        widgets = {
            'subject': forms.TextInput(
                attrs={
                    'placeholder': 'Subject (optional)',
                    'maxlength': '120',
                    'class': 'w-full bg-gray-700 text-white border border-gray-600 rounded-lg px-4 py-3 outline-none focus:border-emerald-500',
                }
            ),
            'message': forms.Textarea(
                attrs={
                    'rows': 5,
                    'placeholder': 'Describe your issue / feedback…',
                    'maxlength': '2000',
                    'class': 'w-full bg-gray-700 text-white border border-gray-600 rounded-lg px-4 py-3 outline-none focus:border-emerald-500',
                }
            ),
        }