using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.Windows.Widgets;
using Microsoft.Windows.Widgets.Providers;

namespace DeskWidget;

/// <summary>Builds the widget's Adaptive Card from the app's "state" reply.</summary>
internal static class Cards
{
    private static string Cm(double value) => value.ToString("0.0", CultureInfo.InvariantCulture);

    public static string Build(JsonObject? state, WidgetSize size)
    {
        if (state is null)
        {
            return Card(
                Text("Desk Control isn't running", size: "Medium", weight: "Bolder"),
                Text("Open it to connect to your desk.", subtle: true),
                Actions(Execute("Open Desk Control", "open")));
        }

        var connected = (string?)state["state"] == "connected";
        var height = (double?)state["height_cm"];
        var speed = (int?)state["speed"] ?? 0;
        var presets = state["presets"]?.AsArray() ?? [];

        var current = presets.Select((p, i) => (Name: (string?)p!["name"], Height: (double)p!["height_cm"]!, Index: i))
            .FirstOrDefault(p => height is not null && Math.Abs(p.Height - height.Value) < 0.5);

        var status = (string?)state["state"] == "nodesk" ? "No desk selected. Choose one in Desk Control."
            : !connected ? "Connecting to desk…"
            : speed > 0 ? "Moving up…"
            : speed < 0 ? "Moving down…"
            : current.Name is not null ? $"At {current.Name}"
            : "Connected";

        var presetActions = new List<JsonNode>();
        for (var i = 0; i < presets.Count; i++)
        {
            var name = (string?)presets[i]!["name"] ?? $"Preset {i + 1}";
            var title = size == WidgetSize.Small ? name : $"{name} · {Cm((double)presets[i]!["height_cm"]!)}";
            var action = Execute(title, "preset", new JsonObject { ["index"] = i }, enabled: connected);
            if (current.Name is not null && current.Index == i)
                action["style"] = "positive";
            presetActions.Add(action);
        }

        var body = new List<JsonNode>
        {
            new JsonObject
            {
                ["type"] = "ColumnSet",
                ["columns"] = new JsonArray(
                    new JsonObject
                    {
                        ["type"] = "Column", ["width"] = "auto",
                        ["items"] = new JsonArray(Text(height is null ? "--" : Cm(height.Value), size: "ExtraLarge", weight: "Bolder")),
                    },
                    new JsonObject
                    {
                        ["type"] = "Column", ["width"] = "stretch", ["verticalContentAlignment"] = "Bottom",
                        ["items"] = new JsonArray(Text("cm", size: "Medium")),
                    }),
            },
            Text(status, subtle: true, spacing: "None"),
        };
        if (presets.Count == 0)
            body.Add(Text("Save presets in the Desk Control app.", subtle: true, spacing: "Medium"));
        else
            body.Add(Actions([.. presetActions]));
        if (size != WidgetSize.Small)
            body.Add(Actions(Execute("Stop", "stop", enabled: connected)));
        return Card([.. body]);
    }

    private static string Card(params JsonNode[] body) => new JsonObject
    {
        ["type"] = "AdaptiveCard",
        ["$schema"] = "http://adaptivecards.io/schemas/adaptive-card.json",
        ["version"] = "1.5",
        ["body"] = new JsonArray(body),
    }.ToJsonString();

    private static JsonObject Text(string text, string? size = null, string? weight = null, bool subtle = false, string? spacing = null)
    {
        var node = new JsonObject { ["type"] = "TextBlock", ["text"] = text, ["wrap"] = true };
        if (size is not null) node["size"] = size;
        if (weight is not null) node["weight"] = weight;
        if (subtle) node["isSubtle"] = true;
        if (spacing is not null) node["spacing"] = spacing;
        return node;
    }

    private static JsonObject Actions(params JsonNode[] actions) => new()
    {
        ["type"] = "ActionSet",
        ["actions"] = new JsonArray(actions),
    };

    private static JsonObject Execute(string title, string verb, JsonObject? data = null, bool enabled = true)
    {
        var node = new JsonObject { ["type"] = "Action.Execute", ["title"] = title, ["verb"] = verb };
        if (data is not null) node["data"] = data;
        if (!enabled) node["isEnabled"] = false;
        return node;
    }
}
