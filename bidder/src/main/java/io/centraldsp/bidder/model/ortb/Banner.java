package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import java.util.List;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class Banner {
    @JsonProperty("w")  private Integer w;
    @JsonProperty("h")  private Integer h;
    @JsonProperty("format") private List<Format> format;
    @JsonProperty("pos")    private Integer pos;

    @Data
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class Format {
        @JsonProperty("w") private Integer w;
        @JsonProperty("h") private Integer h;
    }
}
