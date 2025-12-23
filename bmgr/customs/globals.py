from jinja2 import pass_context

@pass_context
def boot_context(context):
    return dict(sorted(context.items()))

GLOBALS = {
    "__boot_context__": boot_context,
}
