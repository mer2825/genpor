from allauth.account.forms import SignupForm
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV2Checkbox
from paypal.standard.forms import PayPalPaymentsForm
from django.conf import settings
from .models import CompanySettings

class CustomSignupForm(SignupForm):
    captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox)

    def save(self, request):
        user = super(CustomSignupForm, self).save(request)
        return user

class DynamicPayPalPaymentsForm(PayPalPaymentsForm):
    def get_endpoint(self):
        """
        Override to determine Sandbox vs Live based on DB settings instead of settings.py
        """
        company_settings = CompanySettings.objects.first()
        is_test = True # Default to safe mode
        
        if company_settings:
            is_test = company_settings.paypal_is_sandbox
        
        if is_test:
            return "https://www.sandbox.paypal.com/cgi-bin/webscr"
        else:
            return "https://www.paypal.com/cgi-bin/webscr"
