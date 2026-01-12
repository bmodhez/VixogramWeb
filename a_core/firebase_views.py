import json

from django.conf import settings
from django.http import HttpResponse


def firebase_messaging_sw(request):
    """Service worker for Firebase Cloud Messaging.

    Must be served at site root for proper scope.
    """
    enabled = bool(getattr(settings, 'FIREBASE_ENABLED', False))
    cfg = {
        'apiKey': getattr(settings, 'FIREBASE_API_KEY', ''),
        'authDomain': getattr(settings, 'FIREBASE_AUTH_DOMAIN', ''),
        'projectId': getattr(settings, 'FIREBASE_PROJECT_ID', ''),
        'storageBucket': getattr(settings, 'FIREBASE_STORAGE_BUCKET', ''),
        'messagingSenderId': getattr(settings, 'FIREBASE_MESSAGING_SENDER_ID', ''),
        'appId': getattr(settings, 'FIREBASE_APP_ID', ''),
        'measurementId': getattr(settings, 'FIREBASE_MEASUREMENT_ID', ''),
    }
    required = ['apiKey', 'authDomain', 'projectId', 'messagingSenderId', 'appId']
    ready = enabled and all((cfg.get(k) or '').strip() for k in required)

    if not ready:
        # Valid JS; keeps registration from crashing.
        return HttpResponse("// FCM disabled\n", content_type='application/javascript')

    cfg_json = json.dumps(cfg)

    js = f"""// Firebase Messaging service worker
importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.7.1/firebase-messaging-compat.js');

firebase.initializeApp({cfg_json});

const messaging = firebase.messaging();

messaging.onBackgroundMessage((payload) => {{
  try {{
    const notif = (payload && payload.notification) || {{}};
    const data = (payload && payload.data) || {{}};
    const title = notif.title || data.title || 'Vixo Connect';
    const options = {{
      body: notif.body || data.body || '',
      icon: notif.icon || '/static/favicon.png',
      data: {{
        url: data.url || '/',
      }},
    }};
    self.registration.showNotification(title, options);
  }} catch (e) {{
    // ignore
  }}
}});

self.addEventListener('notificationclick', function(event) {{
  event.notification.close();
  const url = (event.notification && event.notification.data && event.notification.data.url) || '/';
  const targetUrl = (() => {{
    try {{
      return new URL(url, self.location.origin).href;
    }} catch {{
      return self.location.origin + '/';
    }}
  }})();
  event.waitUntil(
    clients.matchAll({{ type: 'window', includeUncontrolled: true }}).then((clientList) => {{
      for (const client of clientList) {{
        if (client.url === targetUrl && 'focus' in client) return client.focus();
      }}
      if (clients.openWindow) return clients.openWindow(targetUrl);
    }})
  );
}});
"""

    return HttpResponse(js, content_type='application/javascript')
