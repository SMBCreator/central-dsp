package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import java.util.List;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class Video {
    @JsonProperty("mimes")     private List<String> mimes;
    @JsonProperty("minduration") private Integer minduration;
    @JsonProperty("maxduration") private Integer maxduration;
    @JsonProperty("w")         private Integer w;
    @JsonProperty("h")         private Integer h;
    @JsonProperty("protocols") private List<Integer> protocols;
}
