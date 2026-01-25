from __future__ import annotations

from django.db import models


class SiteSetting(models.Model):
	key = models.CharField(max_length=100, unique=True)
	bool_value = models.BooleanField(default=False)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		indexes = [
			models.Index(fields=['key']),
		]

	def __str__(self) -> str:
		return f"{self.key}={self.bool_value}"

	@classmethod
	def get_bool(cls, key: str, default: bool = False) -> bool:
		try:
			return bool(cls.objects.only('bool_value').get(key=key).bool_value)
		except cls.DoesNotExist:
			return bool(default)
		except Exception:
			return bool(default)

	@classmethod
	def set_bool(cls, key: str, value: bool) -> bool:
		try:
			obj, _created = cls.objects.update_or_create(key=key, defaults={'bool_value': bool(value)})
			return bool(obj.bool_value)
		except Exception:
			return bool(value)
