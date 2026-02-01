from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('a_users', '0012_profile_cover_image'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='is_founder_club',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='profile',
            name='founder_club_granted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='founder_club_revoked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='founder_club_reapply_available_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='founder_club_last_checked',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='DailyUserActivity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(db_index=True)),
                ('active_seconds', models.PositiveIntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_activity', to='auth.user')),
            ],
            options={
                'indexes': [models.Index(fields=['user', '-date'], name='dua_user_date_idx')],
            },
        ),
        migrations.AddConstraint(
            model_name='dailyuseractivity',
            constraint=models.UniqueConstraint(fields=('user', 'date'), name='uniq_daily_user_activity_user_date'),
        ),
    ]
