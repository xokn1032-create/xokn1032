he MachineName contains malicious JavaScript:

GO

package main

import "fmt"

// Simulated structure representing the incoming NotificationEvent 
// as it might be parsed from JSON/Database before rendering.
type NotificationEvent struct {
	MachineName   string // <--- Potential Injection Point!
	DashboardGroupInfo string
	ValidatorIndex uint64
}

func main() {
	// 1. Define the Malicious Input (The Attack)
	maliciousInput := `"MonitorMachine"</span><script>alert('XSS')</script>Official_Validator`

	event := NotificationEvent{
		MachineName:        maliciousInput, // Direct assignment without sanitization check in this demo
		DashboardGroupInfo: "Mainnet",
		ValidatorIndex:     12345,
	}

	fmt.Printf("🏁 Injected Payload Received:\n%s\n", event.MachineName)
	fmt.Println("\n--- 🎴 Simulating Backend Rendering (Vulnerable Flow) ---\n")

	// 2. The Vulnerable Renderer (Mimicking the backend's HTML formatter before fix)
	renderHTML(event)
	
	fmt.Println("\n--- 🏴 Resulting Output on Browser/Client ---")
	fmt.Println("<html>... <body>")
	fmt.Printf("    <span class='machine-name'>%s</span>\n", escapeBasic(event.MachineName)) 
	fmt.Println("</body></html>")
}

// Mimics a basic or missing sanitization layer in the original code context
func renderHTML(e NotificationEvent) {
	fmt.Printf("Processing Type: HTML\n")
	fmt.Printf("Input String: %q\n", e.MachineName)
	
	// If backend just does fmt.Sprintf into an <b>%s</b> tag without full regex/html entity check,
	// and if it only escapes simple chars but misses nested tags or attributes...
	
	// Example of what might happen if `format` is HTML and sanitization was partial/missing for this field:
	finalOutput := fmt.Sprintf(`Validator <b>%s</b> is offline`, escapeBasic(e.MachineName))

	if containsJS(finalOutput) {
		fmt.Println("⚠️  WARNING: Script-like pattern detected in output!")
	} else {
		fmt.Println("✅ Output seems safe (after basic escaping).")
	}

	fmt.Println("\nFinal Rendered String:")
	fmt.Printf("%q\n", finalOutput) 
}

// Helper to check if the string contains JS keywords after simple processing
func containsJS(s string) bool {
	return len(s) > 0 && s[len(s)-1] != '>' // Heuristic for unclosed tags, simplified here.
}

// Basic escape often found initially, might miss <script> inside attributes or complex nesting depending on impl
func escapeBasic(s string) string {
	replacer := strings.NewReplacer(
		"<", "&lt;", ">","&gt;" , "\"", "&quot;")
	return replacer.Replace(strings.TrimSpace(s))
}

import "strings" 
