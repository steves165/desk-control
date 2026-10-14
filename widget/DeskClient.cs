using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace DeskWidget;

/// <summary>Talks to the Desk Control app over its named pipe; the app owns the Bluetooth connection.</summary>
internal static class DeskClient
{
    private const string PipeName = "DeskControl";

    /// <returns>The app's reply, or null if the app isn't running.</returns>
    public static async Task<JsonObject?> SendAsync(object request)
    {
        using var pipe = new NamedPipeClientStream(".", PipeName, PipeDirection.InOut, PipeOptions.Asynchronous);
        try
        {
            await pipe.ConnectAsync(500);
            var bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(request) + "\n");
            await pipe.WriteAsync(bytes);
            await pipe.FlushAsync();
            using var reader = new StreamReader(pipe, Encoding.UTF8);
            var line = await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(2));
            return line is null ? null : JsonNode.Parse(line) as JsonObject;
        }
        catch (Exception e) when (e is TimeoutException or IOException)
        {
            return null;
        }
    }
}
