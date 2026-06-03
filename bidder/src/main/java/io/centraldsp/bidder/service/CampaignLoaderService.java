package io.centraldsp.bidder.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.centraldsp.bidder.model.campaign.CampaignDTO;
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
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Periodically fetches active campaigns from Central DSP backend
 * and caches them in-memory for fast bid evaluation (<1ms lookup).
 *
 * Schedule: every 30 seconds (configurable).
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class CampaignLoaderService {

    @Value("${campaigns.rest.url}")
    private String campaignsUrl;

    @Value("${campaigns.rest.token:changeme-set-in-env}")
    private String apiToken;

    private final ObjectMapper objectMapper;
    private final BudgetService budgetService;

    // Thread-safe campaign store: campaignId -> CampaignDTO
    private final AtomicReference<Map<Long, CampaignDTO>> campaignCache =
            new AtomicReference<>(new ConcurrentHashMap<>());

    /** Load campaigns immediately on startup, then every 30s. */
    @PostConstruct
    public void init() {
        loadCampaigns();
    }

    @Scheduled(fixedDelayString = "${campaigns.update.interval:30}000")
    public void scheduledLoad() {
        loadCampaigns();
    }

    /**
     * Fetches campaigns list from backend REST API.
     * On error, keeps the previous cache intact.
     */
    private void loadCampaigns() {
        try (CloseableHttpClient httpClient = HttpClients.createDefault()) {
            HttpGet request = new HttpGet(campaignsUrl);
            request.setHeader("Authorization", "Bearer " + apiToken);
            request.setHeader("Accept", "application/json");

            String responseBody = httpClient.execute(request, response -> {
                int statusCode = response.getCode();
                if (statusCode != 200) {
                    log.warn("[CampaignLoader] Backend returned HTTP {}", statusCode);
                    return null;
                }
                return EntityUtils.toString(response.getEntity());
            });

            if (responseBody == null) return;

            // Backend returns: { "campaigns": [...], "total": N }
            Map<String, Object> envelope = objectMapper.readValue(
                    responseBody, new TypeReference<>() {});

            @SuppressWarnings("unchecked")
            List<Object> rawList = (List<Object>) envelope.get("campaigns");
            List<CampaignDTO> campaigns = objectMapper.convertValue(
                    rawList, new TypeReference<>() {});

            Map<Long, CampaignDTO> newCache = new ConcurrentHashMap<>();
            for (CampaignDTO c : campaigns) {
                // Only cache active campaigns with at least one approved creative
                if ("active".equals(c.getStatus()) && hasApprovedCreative(c)) {
                    newCache.put(c.getId(), c);
                    // Sync budget state to Redis
                    budgetService.initBudget(c);
                }
            }

            campaignCache.set(newCache);
            log.info("[CampaignLoader] Loaded {} active campaigns", newCache.size());

        } catch (Exception e) {
            log.error("[CampaignLoader] Failed to load campaigns: {}", e.getMessage());
        }
    }

    /** Returns snapshot of current active campaigns. */
    public List<CampaignDTO> getActiveCampaigns() {
        return List.copyOf(campaignCache.get().values());
    }

    /** Returns specific campaign by ID, or null. */
    public CampaignDTO getCampaign(Long id) {
        return campaignCache.get().get(id);
    }

    private boolean hasApprovedCreative(CampaignDTO c) {
        if (c.getCreatives() == null || c.getCreatives().isEmpty()) return false;
        return c.getCreatives().stream()
                .anyMatch(cr -> "approved".equals(cr.getStatus()));
    }
}
