from django.test import TestCase
from django.urls import reverse


class HealthEndpointTests(TestCase):
    def test_health_endpoint_returns_status(self):
        """Health endpoint should return a JSON status object with db and redis keys."""
        url = reverse('health')
        resp = self.client.get(url)
        self.assertIn(resp.status_code, (200, 500))
        data = resp.json()
        # root structure
        self.assertIn('status', data)
        status = data['status']
        # expect keys set by health view
        self.assertIn('db', status)
        self.assertIn('redis', status)
from django.test import TestCase

# Create your tests here.
