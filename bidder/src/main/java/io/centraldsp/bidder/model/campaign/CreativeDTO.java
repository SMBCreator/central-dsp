package io.centraldsp.bidder.model.campaign;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * Creative DTO deserialized from /api/rtb/campaigns response.
 * Mirrors Python's CreativeOut schema.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class CreativeDTO {

    @JsonProperty("id")
    private Long id;

    /**
     * Ad format: "banner", "native", "video".
     */
    @JsonProperty("format")
    private String format;

    /** Banner width in pixels. */
    @JsonProperty("width")
    private Integer width;

    /** Banner height in pixels. */
    @JsonProperty("height")
    private Integer height;

    // ── Banner fields ──────────────────────────────────────────

    /** Image asset URL (banner). */
    @JsonProperty("image_url")
    private String imageUrl;

    /** Click-through URL. */
    @JsonProperty("click_url")
    private String clickUrl;

    // ── Native fields ──────────────────────────────────────────

    /** Native ad headline. */
    @JsonProperty("title")
    private String title;

    /** Native ad description / body text. */
    @JsonProperty("description")
    private String description;

    /** Native icon image URL. */
    @JsonProperty("icon_url")
    private String iconUrl;

    /** Sponsored-by label for native ads. */
    @JsonProperty("sponsored_by")
    private String sponsoredBy;

    // ── Video fields ───────────────────────────────────────────

    /** VAST XML URL for video creatives. */
    @JsonProperty("vast_url")
    private String vastUrl;

    /** Direct MP4/WebM URL for video creatives. */
    @JsonProperty("video_url")
    private String videoUrl;

    /** Video duration in seconds. */
    @JsonProperty("duration_sec")
    private Integer durationSec;

    // ── Raw markup ─────────────────────────────────────────────

    /**
     * Raw ad markup (HTML for banner, VAST XML for video, JSON for native).
     * If present, used directly as OpenRTB adm field.
     */
    @JsonProperty("adm")
    private String adm;

    /**
     * Advertiser's top-level domain for OpenRTB adomain field.
     * E.g. "example.com". If null, adomain list will be empty.
     */
    @JsonProperty("adomain")
    private String adomain;

    /**
     * Convenience: returns true if this creative has enough data to be served.
     */
    public boolean isServable() {
        if (format == null) return false;
        return switch (format) {
            case "banner" -> adm != null || imageUrl != null;
            case "native" -> title != null && clickUrl != null;
            case "video"  -> vastUrl != null || videoUrl != null || adm != null;
            default       -> false;
        };
    }
}
