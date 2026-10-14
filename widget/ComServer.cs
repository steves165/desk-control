using System.Runtime.InteropServices;
using Microsoft.Windows.Widgets.Providers;
using WinRT;

namespace DeskWidget;

/// <summary>Registers the widget provider as an out-of-process COM server for the Widgets host.</summary>
internal static class ComServer
{
    private const uint CLSCTX_LOCAL_SERVER = 0x4;
    private const uint REGCLS_MULTIPLEUSE = 0x1;

    [DllImport("ole32.dll")]
    private static extern int CoRegisterClassObject(
        [MarshalAs(UnmanagedType.LPStruct)] Guid rclsid,
        [MarshalAs(UnmanagedType.IUnknown)] object pUnk,
        uint dwClsContext, uint flags, out uint lpdwRegister);

    [DllImport("ole32.dll")]
    private static extern int CoRevokeClassObject(uint dwRegister);

    public static uint Register<T>() where T : IWidgetProvider, new()
    {
        Marshal.ThrowExceptionForHR(CoRegisterClassObject(
            typeof(T).GUID, new Factory<T>(), CLSCTX_LOCAL_SERVER, REGCLS_MULTIPLEUSE, out var cookie));
        return cookie;
    }

    public static void Revoke(uint cookie) => CoRevokeClassObject(cookie);

    [ComImport, ComVisible(false), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("00000001-0000-0000-C000-000000000046")]
    private interface IClassFactory
    {
        [PreserveSig] int CreateInstance(IntPtr pUnkOuter, ref Guid riid, out IntPtr ppvObject);
        [PreserveSig] int LockServer(bool fLock);
    }

    private sealed class Factory<T> : IClassFactory where T : IWidgetProvider, new()
    {
        private const int CLASS_E_NOAGGREGATION = -2147221232;
        private const int E_NOINTERFACE = -2147467262;
        private static readonly Guid IUnknown = new("00000000-0000-0000-C000-000000000046");

        public int CreateInstance(IntPtr pUnkOuter, ref Guid riid, out IntPtr ppvObject)
        {
            ppvObject = IntPtr.Zero;
            if (pUnkOuter != IntPtr.Zero) return CLASS_E_NOAGGREGATION;
            if (riid != typeof(T).GUID && riid != IUnknown) return E_NOINTERFACE;
            ppvObject = MarshalInspectable<IWidgetProvider>.FromManaged(new T());
            return 0;
        }

        public int LockServer(bool fLock) => 0;
    }
}
