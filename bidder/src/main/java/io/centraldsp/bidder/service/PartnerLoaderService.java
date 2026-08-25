package io.centraldsp.bidder.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.centraldsp.bidder.model.campaign.PartnerDTO;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.apache.hc.client5.http.classic.methods.HttpGet;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.apache.hc.core5.http.io.entity.EntityUtils;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import javax.annotation.PostConstruct;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Periodically fetches active supply partners from the backend feed
 * (/rtb-feed/partners) and caches them by endpoint_key for O(1) lookup
 * on the hot path.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PartnerLoaderService {

    @Value("${partners.rest.url:http://backend:8000/rtb-feed/partners}")
    private String partnersUrl;

    @Value("${campaigns.rest.token:changeme-set-in-env}")
    private String apiToken;

    private final ObjectMapper objectMapper;

    private final AtomicReference<Map<String, PartnerDTO>> cache =
            new AtomicReference<>(new ConcurrentHashMap<>());

    @PostConstruct
    public void init() {
        load();
    }

    @Scheduled(fixedDelayString = "${partners.update.interval:30}000")
    public void scheduledLoad() {
        load();
    }

    public PartnerDTO byEndpointKey(String endpointKey) {
        return cache.get().get(endpointKey);
    }

    private void load() {
        try (CloseableHttpClient http = HttpClients.createDefault()) {
            HttpGet req = new HttpGet(partnersUrl);
            req.setHeader("Authorization", "Bearer " + apiToken);
            String body = http.execute(req, r -> EntityUtils.toString(r.getEntity()));
            JsonNode root = objectMapper.readTree(body);
            JsonNode arr = root.get("partners");
            if (arr == null || !arr.isArray()) {
                log.warn("[Partners] Unexpected feed payload, keeping previous cache");
                return;
            }
            List<PartnerDTO> partners = objectMapper.convertValue(
                    arr, objectMapper.getTypeFactory()
                            .constructCollectionType(List.class, PartnerDTO.class));
            Map<String, PartnerDTO> next = new ConcurrentHashMap<>();
            for (PartnerDTO p : partners) {
                if (p.getEndpointKey() != null) next.put(p.getEndpointKey(), p);
            }
            cache.set(next);
            log.info("[Partners] Loaded {} active partners", next.size());
        } catch (Exception e) {
            log.error("[Partners] Feed fetch failed, keeping previous cache: {}", e.getMessage());
        }
    }
}
