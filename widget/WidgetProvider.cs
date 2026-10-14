using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using Microsoft.Windows.Widgets;
using Microsoft.Windows.Widgets.Providers;

namespace DeskWidget;

[ComVisible(true)]
[ComDefaultInterface(typeof(IWidgetProvider))]
[Guid("FB597772-7FB9-498A-B54C-DDC17E54D538")]
internal sealed class WidgetProvider : IWidgetProvider
{
    public static readonly ManualResetEvent EmptyWidgetListEvent = new(false);

    private sealed class Tracked
    {
        public WidgetSize Size;
        public bool Active;
        public string? LastCard;
    }

    private static readonly object Gate = new();
    private static readonly Dictionary<string, Tracked> Widgets = [];
    private static Timer? poller;
    private static int polling;

    public WidgetProvider()
    {
        // The host may restart us with widgets already pinned.
        foreach (var info in WidgetManager.GetDefault().GetWidgetInfos())
        {
            var context = info.WidgetContext;
            lock (Gate) Widgets.TryAdd(context.Id, new Tracked { Size = context.Size, Active = context.IsActive });
        }
        lock (Gate) poller ??= new Timer(_ => _ = PollAsync(), null, 1000, 1000);
    }

    public void CreateWidget(WidgetContext context)
    {
        lock (Gate) Widgets[context.Id] = new Tracked { Size = context.Size, Active = context.IsActive };
        _ = RefreshAsync(context.Id, force: true);
    }

    public void DeleteWidget(string widgetId, string customState)
    {
        lock (Gate)
        {
            Widgets.Remove(widgetId);
            if (Widgets.Count == 0) EmptyWidgetListEvent.Set();
        }
    }

    public void Activate(WidgetContext context)
    {
        lock (Gate)
        {
            if (!Widgets.TryGetValue(context.Id, out var widget))
                Widgets[context.Id] = widget = new Tracked { Size = context.Size };
            widget.Active = true;
        }
        _ = RefreshAsync(context.Id, force: true);
    }

    public void Deactivate(string widgetId)
    {
        lock (Gate)
            if (Widgets.TryGetValue(widgetId, out var widget)) widget.Active = false;
    }

    public void OnWidgetContextChanged(WidgetContextChangedArgs args)
    {
        var context = args.WidgetContext;
        lock (Gate)
            if (Widgets.TryGetValue(context.Id, out var widget)) widget.Size = context.Size;
        _ = RefreshAsync(context.Id, force: true);
    }

    public void OnActionInvoked(WidgetActionInvokedArgs args)
    {
        var widgetId = args.WidgetContext.Id;
        _ = HandleActionAsync(args.Verb, args.Data, widgetId);
    }

    private static async Task HandleActionAsync(string verb, string? data, string widgetId)
    {
        switch (verb)
        {
            case "preset":
                var index = (int?)JsonNode.Parse(string.IsNullOrEmpty(data) ? "{}" : data)?["index"];
                if (index is not null) await DeskClient.SendAsync(new { cmd = "preset", index });
                break;
            case "stop":
                await DeskClient.SendAsync(new { cmd = "stop" });
                break;
            case "open":
                var shortcut = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "Desk Control.lnk");
                Process.Start(new ProcessStartInfo(shortcut) { UseShellExecute = true });
                await Task.Delay(3000);
                break;
        }
        await RefreshAsync(widgetId, force: true);
    }

    /// <summary>Keeps visible widgets live (height while moving, connection state).</summary>
    private static async Task PollAsync()
    {
        if (Interlocked.Exchange(ref polling, 1) == 1) return;
        try
        {
            string[] active;
            lock (Gate) active = Widgets.Where(w => w.Value.Active).Select(w => w.Key).ToArray();
            if (active.Length == 0) return;
            var state = await DeskClient.SendAsync(new { cmd = "state" });
            foreach (var id in active) Update(id, state, force: false);
        }
        finally
        {
            Volatile.Write(ref polling, 0);
        }
    }

    private static async Task RefreshAsync(string widgetId, bool force)
    {
        var state = await DeskClient.SendAsync(new { cmd = "state" });
        Update(widgetId, state, force);
    }

    private static void Update(string widgetId, JsonObject? state, bool force)
    {
        string card;
        lock (Gate)
        {
            if (!Widgets.TryGetValue(widgetId, out var widget)) return;
            card = Cards.Build(state, widget.Size);
            if (!force && card == widget.LastCard) return;
            widget.LastCard = card;
        }
        WidgetManager.GetDefault().UpdateWidget(new WidgetUpdateRequestOptions(widgetId) { Template = card, Data = "{}" });
    }
}
