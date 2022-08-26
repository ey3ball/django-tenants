from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.http import Http404
from django.urls import set_urlconf, clear_url_caches, path, include
from django_tenants.middleware import TenantMainMiddleware
from django_tenants.utils import (
    get_public_schema_name,
    get_tenant_model,
    get_subfolder_prefix, get_tenant_domain_model,
    has_multi_type_tenants, get_tenant_types,
)


class TenantSubfolderMiddleware(TenantMainMiddleware):
    """
    This middleware should be placed at the very top of the middleware stack.
    Selects the proper tenant using the path subfolder prefix. Can fail in
    various ways which is better than corrupting or revealing data.
    """

    TENANT_NOT_FOUND_EXCEPTION = Http404

    def __init__(self, get_response):
        super().__init__(get_response)
        self.get_response = get_response
        if not get_subfolder_prefix():
            raise ImproperlyConfigured(
                '"TenantSubfolderMiddleware" requires "TENANT_SUBFOLDER_PREFIX" '
                "present and non-empty in settings"
            )

    def get_tenant_urlconf(self, tenant):
        if has_multi_type_tenants():
            urlconf = get_tenant_types()[tenant.get_tenant_type()]['URLCONF']
        else:
            urlconf = settings.ROOT_URLCONF

        subfolder_prefix = get_subfolder_prefix()
        class TenantUrlConf(ModuleType):
            urlpatterns = [
                path(f"{subfolder_prefix}/{tenant.domain_subfolder}/", include(urlconf))
            ]

        return TenantUrlConf(tenant.domain_subfolder)

    def process_request(self, request):
        # Short circuit if tenant is already set by another middleware.
        # This allows for multiple tenant-resolving middleware chained together.
        if hasattr(request, "tenant"):
            return

        connection.set_schema_to_public()

        urlconf = None

        tenant_model = get_tenant_model()
        domain_model = get_tenant_domain_model()
        hostname = self.hostname_from_request(request)
        subfolder_prefix_path = "/{}/".format(get_subfolder_prefix())

        # We are in the public tenant
        if not request.path.startswith(subfolder_prefix_path):
            try:
                tenant = tenant_model.objects.get(schema_name=get_public_schema_name())
            except tenant_model.DoesNotExist:
                raise self.TENANT_NOT_FOUND_EXCEPTION("Unable to find public tenant")

            self.setup_url_routing(request, force_public=True)

        # We are in a specific tenant
        else:
            path_chunks = request.path[len(subfolder_prefix_path):].split("/")
            tenant_subfolder = path_chunks[0]
            try:
                tenant = self.get_tenant(domain_model=domain_model, hostname=tenant_subfolder)
            except domain_model.DoesNotExist:
                return self.no_tenant_found(request, hostname)

            tenant.domain_subfolder = tenant_subfolder
            urlconf = self.get_tenant_urlconf(tenant)

        tenant.domain_url = hostname
        request.tenant = tenant

        connection.set_tenant(request.tenant)
        clear_url_caches()  # Required to remove previous tenant prefix from cache, if present

        if urlconf:
            request.urlconf = urlconf
            set_urlconf(urlconf)

    def no_tenant_found(self, request, hostname):
        """ What should happen if no tenant is found.
        This makes it easier if you want to override the default behavior """
        raise self.TENANT_NOT_FOUND_EXCEPTION('No tenant for subfolder "%s"' % hostname)
