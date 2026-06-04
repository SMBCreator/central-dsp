package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * OpenRTB 2.5 §3.2.18 — Device object.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class Device {

    /** Browser user agent string. */
    @JsonProperty("ua")
    private String ua;

    /** Geo information derived from IP. */
    @JsonProperty("geo")
    private Geo geo;

    /** IPv4 address closest to the device. */
    @JsonProperty("ip")
    private String ip;

    /** IPv6 address. */
    @JsonProperty("ipv6")
    private String ipv6;

    /**
     * Device type as defined in OpenRTB List 5.21.
     * 1=Mobile/Tablet, 2=Personal Computer, 3=Connected TV,
     * 4=Phone, 5=Tablet, 6=Connected Device, 7=Set Top Box.
     */
    @JsonProperty("devicetype")
    private Integer deviceType;

    /** Device make (e.g. "Apple"). */
    @JsonProperty("make")
    private String make;

    /** Device model (e.g. "iPhone"). */
    @JsonProperty("model")
    private String model;

    /** Device operating system (e.g. "iOS"). */
    @JsonProperty("os")
    private String os;

    /** Device operating system version (e.g. "3.1.2"). */
    @JsonProperty("osv")
    private String osv;

    /** Browser language using ISO-639-1-alpha-2. */
    @JsonProperty("language")
    private String language;

    /** Carrier or ISP (e.g. "VERIZON" or "310-005"). */
    @JsonProperty("carrier")
    private String carrier;

    /**
     * Network connection type.
     * 0=Unknown, 1=Ethernet, 2=WIFI, 3=Cellular Network Unknown,
     * 4=2G, 5=3G, 6=4G, 7=5G.
     */
    @JsonProperty("connectiontype")
    private Integer connectionType;

    /** ID sanctioned for advertiser use (IDFA / GAID). */
    @JsonProperty("ifa")
    private String ifa;

    /** Hardware device ID (SHA1 hash). */
    @JsonProperty("didsha1")
    private String didSha1;

    /** Hardware device ID (MD5 hash). */
    @JsonProperty("didmd5")
    private String didMd5;

    /** Platform device ID (e.g. Android ID) (SHA1 hash). */
    @JsonProperty("dpidsha1")
    private String dpidSha1;

    /** Platform device ID (MD5 hash). */
    @JsonProperty("dpidmd5")
    private String dpidMd5;

    /** MAC address of device (SHA1 hash). */
    @JsonProperty("macsha1")
    private String macSha1;

    /** MAC address of device (MD5 hash). */
    @JsonProperty("macmd5")
    private String macMd5;

    /**
     * Support for JavaScript (0=No, 1=Yes).
     */
    @JsonProperty("js")
    private Integer js;

    /** Browser name (e.g. "Chrome"). */
    @JsonProperty("browser")
    private String browser;

    /** Screen width of the device in pixels. */
    @JsonProperty("w")
    private Integer w;

    /** Screen height of the device in pixels. */
    @JsonProperty("h")
    private Integer h;

    /**
     * Convenience: returns a readable device type string for targeting.
     * Maps OpenRTB devicetype integer to DSP-internal label.
     */
    public String resolveDeviceTypeLabel() {
        if (deviceType == null) return "unknown";
        return switch (deviceType) {
            case 1 -> "mobile";
            case 2 -> "desktop";
            case 3 -> "ctv";
            case 4 -> "phone";
            case 5 -> "tablet";
            case 6 -> "connected_device";
            case 7 -> "stb";
            default -> "unknown";
        };
    }
}
