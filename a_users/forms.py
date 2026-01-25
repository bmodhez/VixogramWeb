from django import forms
from .models import Profile
from .models import UserReport
from .models import SupportEnquiry
import re


def mask_bio_text(value: str) -> str:
    """Mask links and social handles in bio with asterisks.

    This matches the behavior of the `sanitize_bio` template filter, but
    is applied on save so stored bios stay safe too.
    """
    if not value:
        return value

    text = str(value)
    if not text.strip():
        return text

    patterns = [
        (r'https?://[^\s]+', '*****'),
        (r'www\.[^\s]+', '*****'),
        (r'@[\w\.]+', '*****'),
        (r'(?i)\b(instagram|insta|facebook|fb|tiktok|tik\s*tok|snapchat|snap|telegram|discord|youtube|whatsapp|whats\s*app|twitter|x\.com)\b', '*****'),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['image', 'cover_image', 'displayname', 'info']
        labels = {
            'info': 'Bio',
            'cover_image': 'Profile background',
        }
        widgets = {
            'image': forms.FileInput(attrs={
                'class': 'w-full text-sm text-gray-300 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-gray-700 file:text-white hover:file:bg-gray-600',
            }),
            'cover_image': forms.FileInput(attrs={
                'class': 'w-full text-sm text-gray-300 file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:bg-gray-700 file:text-white hover:file:bg-gray-600',
                'accept': 'image/*',
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

    def clean_cover_image(self):
        f = self.cleaned_data.get('cover_image')
        if not f:
            return f

        # Enforce a small upload limit for profile background.
        try:
            if hasattr(f, 'size') and int(f.size) > 2 * 1024 * 1024:
                raise forms.ValidationError('Background image must be under 2MB.')
        except Exception:
            pass

        # Basic content-type check.
        ct = getattr(f, 'content_type', '') or ''
        if ct and not str(ct).lower().startswith('image/'):
            raise forms.ValidationError('Please upload a valid image file.')

        return f

    def clean_info(self):
        bio = self.cleaned_data.get('info')
        return mask_bio_text(bio)


class ReportUserForm(forms.Form):
    reason = forms.ChoiceField(
        choices=UserReport.REASON_CHOICES,
        widget=forms.Select(
            attrs={
                'class': 'w-full bg-gray-700 text-white border border-gray-600 rounded-lg px-4 py-3 outline-none focus:border-emerald-500',
            }
        ),
    )

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