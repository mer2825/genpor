from allauth.account.forms import SignupForm
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV2Checkbox

class CustomSignupForm(SignupForm):
    captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox)

    def save(self, request):
        user = super(CustomSignupForm, self).save(request)
        return user
