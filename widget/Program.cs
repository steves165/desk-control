using DeskWidget;

// Launched by the Widgets host via the package's COM server registration.
WinRT.ComWrappersSupport.InitializeComWrappers();
if (args.Contains("-RegisterProcessAsComServer"))
{
    var cookie = ComServer.Register<WidgetProvider>();
    WidgetProvider.EmptyWidgetListEvent.WaitOne();
    ComServer.Revoke(cookie);
}
