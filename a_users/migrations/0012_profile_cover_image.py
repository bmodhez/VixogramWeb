from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('a_users', '0011_support_enquiry_reply'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='cover_image',
            field=models.ImageField(blank=True, null=True, upload_to='profile_covers/'),
        ),
    ]
