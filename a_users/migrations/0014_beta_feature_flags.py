from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('a_users', '0013_founder_club_and_daily_activity'),
    ]

    operations = [
        migrations.CreateModel(
            name='BetaFeature',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(unique=True)),
                ('title', models.CharField(max_length=120)),
                ('description', models.TextField(blank=True, default='')),
                ('is_enabled', models.BooleanField(db_index=True, default=False)),
                ('requires_founder_club', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['slug'],
            },
        ),
    ]
