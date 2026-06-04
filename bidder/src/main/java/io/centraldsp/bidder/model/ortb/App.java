package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * OpenRTB 2.5 §3.2.14 — App object.
 * Details about the publisher's mobile app.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class App {

    /** Exchange-specific app ID. */
    @JsonProperty("id")
    private String id;

    /** App name. */
    @JsonProperty("name")
    private String name;

    /** A platform-specific application identifier (bundle / package name). */
    @JsonProperty("bundle")
    private String bundle;

    /** Domain of the app (e.g., "mygame.foo.com"). */
    @JsonProperty("domain")
    private String domain;

    /** App store URL for an installed app. */
    @JsonProperty("storeurl")
    private String storeUrl;

    /** Array of IAB content categories of the app. */
    @JsonProperty("cat")
    private List<String> cat;

    /** Application version. */
    @JsonProperty("ver")
    private String ver;

    /** Comma separated list of keywords about the app. */
    @JsonProperty("keywords")
    private String keywords;

    /** Details about the Publisher. */
    @JsonProperty("publisher")
    private Publisher publisher;

    /** Indicates if the app has a privacy policy (1=yes, 0=no). */
    @JsonProperty("privacypolicy")
    private Integer privacyPolicy;
}
