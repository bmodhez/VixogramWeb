from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('a_rtchat', '0026_globalannouncement'),
    ]

    operations = [
        migrations.CreateModel(
            name='CodeRoomJoinRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('admitted_at', models.DateTimeField(blank=True, null=True)),
                ('admitted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='admitted_code_room_join_requests', to='auth.user')),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='code_room_join_requests', to='a_rtchat.chatgroup')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='code_room_join_requests', to='auth.user')),
            ],
            options={
                'indexes': [models.Index(fields=['room', 'admitted_at', '-created_at'], name='crjr_room_status_created_idx')],
            },
        ),
        migrations.AddConstraint(
            model_name='coderoomjoinrequest',
            constraint=models.UniqueConstraint(fields=('room', 'user'), name='uniq_code_room_join_request_room_user'),
        ),
    ]
