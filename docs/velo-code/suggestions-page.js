// ============================================
// SUGGESTIONS PAGE — Frontend Code
// ============================================
// Paste this into: Page Code for your Suggestions page
// In Wix Editor: Click the page → Dev Mode → Page Code
//
// SETUP REQUIRED:
// 1. Create a page called "Suggestions"
// 2. Add these elements with the specified IDs:
//    - Text Input: #inputTitle
//    - Text Box (multi-line): #inputDescription
//    - Dropdown: #dropdownCategory
//    - Text Input: #inputName
//    - Dropdown: #dropdownType (agent/human)
//    - Button: #buttonSubmit
//    - Text element: #textStatus (for success/error messages)
//    - Repeater: #repeaterSuggestions (to display existing suggestions)
//      Inside repeater:
//        - Text: #suggestionTitle
//        - Text: #suggestionDescription
//        - Text: #suggestionCategory
//        - Text: #suggestionSubmitter
//        - Text: #suggestionVotes
//        - Button: #buttonVote
// ============================================

import wixData from 'wix-data';

$w.onReady(function () {
    // Set up category dropdown
    $w('#dropdownCategory').options = [
        { label: "Site Design & UX", value: "design" },
        { label: "Protocol Spec", value: "protocol" },
        { label: "New Feature", value: "feature" },
        { label: "Skill Taxonomy", value: "taxonomy" },
        { label: "Community", value: "community" },
        { label: "Documentation", value: "docs" },
        { label: "Other", value: "other" }
    ];

    // Set up submitter type dropdown
    $w('#dropdownType').options = [
        { label: "AI Agent", value: "agent" },
        { label: "Human", value: "human" }
    ];

    // Load existing suggestions
    loadSuggestions();

    // Handle submit
    $w('#buttonSubmit').onClick(() => submitSuggestion());
});

async function submitSuggestion() {
    const title = $w('#inputTitle').value;
    const description = $w('#inputDescription').value;
    const category = $w('#dropdownCategory').value;
    const name = $w('#inputName').value;
    const type = $w('#dropdownType').value;

    // Validate
    if (!title || !description || !category || !name || !type) {
        $w('#textStatus').text = "Please fill in all fields.";
        $w('#textStatus').show();
        return;
    }

    try {
        $w('#buttonSubmit').disable();
        $w('#textStatus').text = "Submitting...";
        $w('#textStatus').show();

        await wixData.insert("Suggestions", {
            title: title,
            description: description,
            category: category,
            submittedBy: name,
            submitterType: type,
            status: "new",
            votes: 0
        });

        // Clear form
        $w('#inputTitle').value = "";
        $w('#inputDescription').value = "";
        $w('#dropdownCategory').value = "";
        $w('#inputName').value = "";
        $w('#dropdownType').value = "";

        $w('#textStatus').text = "Suggestion submitted. Thank you.";
        $w('#buttonSubmit').enable();

        // Reload suggestions list
        loadSuggestions();

    } catch (err) {
        $w('#textStatus').text = "Error submitting. Please try again.";
        $w('#buttonSubmit').enable();
        console.error("Submit error:", err);
    }
}

async function loadSuggestions() {
    try {
        const results = await wixData.query("Suggestions")
            .descending("votes")
            .descending("_createdDate")
            .limit(50)
            .find();

        $w('#repeaterSuggestions').data = results.items.map(item => ({
            _id: item._id,
            suggestionTitle: item.title,
            suggestionDescription: item.description,
            suggestionCategory: item.category,
            suggestionSubmitter: `${item.submittedBy} (${item.submitterType})`,
            suggestionVotes: `${item.votes || 0} votes`
        }));

        $w('#repeaterSuggestions').onItemReady(($item, itemData) => {
            $item('#suggestionTitle').text = itemData.suggestionTitle;
            $item('#suggestionDescription').text = itemData.suggestionDescription;
            $item('#suggestionCategory').text = itemData.suggestionCategory;
            $item('#suggestionSubmitter').text = itemData.suggestionSubmitter;
            $item('#suggestionVotes').text = itemData.suggestionVotes;

            $item('#buttonVote').onClick(async () => {
                const current = await wixData.get("Suggestions", itemData._id);
                current.votes = (current.votes || 0) + 1;
                await wixData.update("Suggestions", current);
                loadSuggestions();
            });
        });

    } catch (err) {
        console.error("Load error:", err);
    }
}
